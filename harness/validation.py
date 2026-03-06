import concurrent.futures
import json
import os
import re
import docker
import pandas as pd
from tqdm import tqdm
from pathlib import Path
import shutil
import time
import datetime
from collections import Counter
from pwd import getpwnam
from dotenv import load_dotenv


load_dotenv()

if os.geteuid() != 0:
    raise PermissionError('please run as root!')
    # because file operations may fail for folders mounted to docker


EVAL_THREADS = 8
EVAL_TIMEOUT_S = 1200
RESULT_PATH = Path("results") 
DOCKER_IMAGE_BASE = os.environ.get('DOCKER_IMAGE_BASE', 'jefzda/sweap-images')
FILE_OWNER_UID = getpwnam(os.getenv('FILE_OWNER', 'root')).pw_uid
ROOTFS_DEVICE = os.getenv('ROOTFS_DEVICE', '/dev/sda')


def ts():
    return datetime.datetime.now().isoformat()


sweap_base = Path("sweap").resolve()

# Credit: prabhuteja12
def load_base_docker(iid):
    with open(f"{sweap_base}/dockerfiles/base_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()

def instance_docker(iid):
    with open(f"{sweap_base}/dockerfiles/instance_dockerfile/{iid}/Dockerfile") as fp:
        return fp.read()

def load_local_script(scripts_dir, instance_id, script_name):
    """Load a script file from local scripts directory."""
    script_path = os.path.join(scripts_dir, instance_id, script_name)
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")
    
    with open(script_path, 'r') as f:
        return f.read()


def strip_binary_hunks(patch: str) -> str:
    """Remove binary diff sections from a git patch."""
    if not patch:
        return patch

    sections = re.split(r'(?=^diff --git )', patch, flags=re.MULTILINE)

    kept: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        if re.search(r'^Binary files .* differ$', section, re.MULTILINE):
            continue
        if re.search(r'^GIT binary patch$', section, re.MULTILINE):
            continue
        kept.append(section)

    return "".join(kept)


def create_entryscript(sample):
    before_repo_set_cmd = sample["before_repo_set_cmd"].strip().split("\n")[-1]
    selected_test_files_to_run = ",".join(eval(sample["selected_test_files_to_run"]))
    base_commit = sample["base_commit"]
    base_dockerfile = load_base_docker(sample["instance_id"])
    instance_dockerfile = instance_docker(sample["instance_id"])
    
    # Extract ENV commands from dockerfiles
    env_cmds = []
    for dockerfile_content in [base_dockerfile, instance_dockerfile]:
        for line in dockerfile_content.split("\n"):
            line = line.strip()
            if line.startswith("ENV"):
                # Convert ENV commands to export statements
                env_cmd = line.replace("ENV", "export", 1)
                env_cmds.append(env_cmd)
    
    env_cmds = "\n".join(env_cmds)

    entry_script = f"""
{env_cmds}
echo === checkout === $(date -Iseconds)
# apply patch
cd /app
git reset --hard {base_commit}
git checkout {base_commit}
echo === apply patch === $(date -Iseconds)
git apply -v --ignore-space-change --ignore-whitespace --inaccurate-eof --reject /workspace/patch.diff
echo === applied diff === $(date -Iseconds)
git diff
echo === run test === $(date -Iseconds)
{before_repo_set_cmd}
# run test and save stdout and stderr to separate files
bash /workspace/run_script.sh {selected_test_files_to_run} > /workspace/stdout.log 2> /workspace/stderr.log
# run parsing script
echo === run parser === $(date -Iseconds)
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
echo === done === $(date -Iseconds)
cat /workspace/output.json
"""
    return entry_script


def create_dockerhub_tag(uid, repo_name=""):
    """
    Convert instance_id and repo name to Docker Hub compatible tag format.
    This must match the format used in the upload script.

    Args:
        uid (str): The instance_id (e.g., "django__django-12345")
        repo_name (str): The repository name from ECR (e.g., "sweap-images/nodebb.nodebb")

    Returns:
        str: Docker Hub compatible tag (e.g., "nodebb-nodebb-12345")
    """
    if repo_name:
        # For "NodeBB/NodeBB" -> repo_base="nodebb", repo_name="nodebb" 
        # Format: {repo_base}.{repo_name}-{OriginalCase}__{OriginalCase}-{hash}-{version}
        # Example: nodebb.nodebb-NodeBB__NodeBB-7b8bffd763e2155cf88f3ebc258fa68ebe18188d-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e
        repo_base, repo_name_only = repo_name.lower().split("/")
        # Keep original case for the instance_id part (after removing "instance_" prefix)
        hsh = uid.replace("instance_", "")
        return f"{repo_base}.{repo_name_only}-{hsh}"
    else:
        image_name = "default"

    # Extract the tag part from the instance ID
    # For UIDs that start with a pattern like "django__django-", extract everything after position 9
    if "__" in uid and len(uid) > 9:
        tag_part = uid[9:]  # Skip the first 9 characters (e.g., "django__")
    else:
        tag_part = uid

    return f"{image_name}-{tag_part}"




def prepare_run(uid, output_dir, prefix, redo):
    output_path = os.path.join(output_dir, f"{prefix}_output.json")
    if not redo and os.path.exists(output_path):
        print(f"Skipping {uid} - output already exists")
        with open(output_path, "r") as f:
            return json.load(f), os.path.join(output_dir, "workspace")
    
    try:
        shutil.rmtree(output_dir, ignore_errors=True)
    except Exception:
        pass
    os.makedirs(output_dir, exist_ok=True)
    os.chown(output_dir, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)
    workspace_dir = os.path.join(output_dir, "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    return None, workspace_dir


def assemble_workspace_files(uid, scripts_dir, patch, sample):
    run_script = load_local_script(scripts_dir, uid, "run_script.sh")
    parser_script = load_local_script(scripts_dir, uid, "parser.py")
    entryscript_content = create_entryscript(sample)

    cleaned_patch = strip_binary_hunks(patch)
    if not cleaned_patch.endswith("\n"):
        cleaned_patch += "\n"
    #if cleaned_patch != patch:
    #    print(f"Stripped binary diff hunks from patch for {uid}")

    files = {
        "patch.diff": cleaned_patch,
        "run_script.sh": run_script,
        "parser.py": parser_script,
        "entryscript.sh": entryscript_content,
    }
    return files, entryscript_content


def write_files_local(workspace_dir, files):
    for rel_path, content in files.items():
        dst = os.path.join(workspace_dir, rel_path)
        with open(dst, "w") as f:
            f.write(content)


def save_entryscript_copy(output_dir, uid, prefix, entryscript_content):
    p = os.path.join(output_dir, f"{prefix}_entryscript.sh")
    with open(p, "w") as f:
        f.write(entryscript_content if entryscript_content is not None else "")
    os.chown(p, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)


def collect_outputs_local(workspace_dir, output_dir, uid, prefix):
    def _copy_safe(src_name, dest_name):
        src_path = os.path.join(workspace_dir, src_name)
        dest_path = os.path.join(output_dir, dest_name)
        try:
            with open(src_path, "r") as f_in:
                content = f_in.read()
        except FileNotFoundError:
            content = ""
        with open(dest_path, "w") as f_out:
            f_out.write(content if content is not None else "")
        os.chown(dest_path, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)

    _copy_safe("stdout.log", f"{prefix}_stdout.log")
    _copy_safe("stderr.log", f"{prefix}_stderr.log")

    # Then try to read output.json
    try:
        with open(os.path.join(workspace_dir, "output.json"), "r") as f_in:
            output = json.load(f_in)
            with open(os.path.join(output_dir, f"{prefix}_output.json"), "w") as f:
                json.dump(output, f)
        
        os.chown(os.path.join(output_dir, f"{prefix}_output.json"), uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)
        return output
    except FileNotFoundError:
        print(
            f"Warning: output.json not found for {uid}. Check {prefix}_stdout.log and {prefix}_stderr.log for details"
        )
        return None


def eval_with_docker(patch, sample, output_dir, prefix="", redo=False, block_network=False):
    scripts_dir = str((sweap_base / 'run_scripts').resolve())
    verdict = {}

    if not patch:
        verdict['status'] = 'nopatch'
        return {'tests': [], 'error': f'no patch: {patch!r}'}, verdict
    
    uid = sample["instance_id"]
    existing_output, workspace_dir = prepare_run(uid, output_dir, prefix, redo)
    if existing_output is not None:
        verdict['status'] = 'cached'
        return existing_output, verdict

    #print(f"{output_dir}: running")

    try:
        try:
            files, entryscript_content = assemble_workspace_files(uid, scripts_dir, patch, sample)
        except FileNotFoundError as e:
            print(f"Error loading scripts for {uid}: {e}")
            verdict['status'] = 'noscript'
            return {'tests': [], 'error': f'no entryscript: {e}'}, verdict
        write_files_local(workspace_dir, files)

        # Run container via Docker SDK
        dockerhub_image_uri = f"{DOCKER_IMAGE_BASE}:{sample['dockerhub_tag']}"
        #print(f"Using Docker Hub image: {dockerhub_image_uri}")

        client = docker.from_env()
        try:
            client.images.get(dockerhub_image_uri)
            #print(f"Using locally available image: {dockerhub_image_uri}")
        except Exception as e:
            print(f"Failed to find image locally for {uid}: {e}")
            verdict['status'] = 'noimage'
            return {'tests': [], 'error': f'no docker image: {e}'}, verdict

        abs_workspace_dir = os.path.abspath(workspace_dir)
        volumes = {abs_workspace_dir: {"bind": "/workspace", "mode": "rw"}}
        run_kwargs = {
            "volumes": volumes,
            "detach": True,
            "remove": False, # because we want to get the stdout
            "entrypoint": "/bin/bash",  # Override image entrypoint
            "command": ["-c", "bash /workspace/entryscript.sh"],
            "cpu_period": 100000,
            "cpu_quota": 100000 * 6,
            "mem_limit": '12g',
            "memswap_limit": '12g',
            "device_read_bps": [{"Path": ROOTFS_DEVICE, "Rate": 30*1024*1024}],
            "device_write_bps": [{"Path": ROOTFS_DEVICE, "Rate": 30*1024*1024}],
            "device_read_iops": [{"Path": ROOTFS_DEVICE, "Rate": 2000}],
            "device_write_iops": [{"Path": ROOTFS_DEVICE, "Rate": 2000}],
            "blkio_weight": 300,
            "pids_limit": 32768,
        }
        if block_network:
            run_kwargs["network_mode"] = "none"

        verdict['ts_begin'] = ts()
        container = client.containers.run(
            dockerhub_image_uri,
            **run_kwargs,
        )

        try:
            result = container.wait(timeout=EVAL_TIMEOUT_S)
        except Exception:
            verdict['status'] = 'timeout'
            return {'tests': [], 'error': 'timeout'}, verdict
        else:
            verdict['status'] = result
        finally:
            verdict['ts_end'] = ts()

            # write validation.log
            try:
                sh_output = container.logs().decode("utf-8", errors='replace')
                log_pn = os.path.join(output_dir, '..', 'validation.log')
                with open(log_pn, "w") as f:
                    f.write(sh_output)
                os.chown(log_pn, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)
            except Exception:
                pass

            # remove container
            try:
                container.remove(force=True)
            except Exception:
                time.sleep(10)
                try:
                    container.remove(force=True)
                except Exception:
                    pass
        
        status_code = result.get("StatusCode", 1) if isinstance(result, dict) else 1
        if status_code != 0:
            print(f"Entryscript failed for {uid} with return code: {status_code}")
        # Collect outputs and logs, and save entryscript for reference
        output = collect_outputs_local(workspace_dir, output_dir, uid, prefix)
        if output is None:
            verdict['status'] = 'nooutput'
            return {'tests': [], 'error': f'no output'}, verdict
        save_entryscript_copy(output_dir, uid, prefix, entryscript_content)

        return output, verdict
    
    except Exception as e:
        print(f"Error in eval_with_docker for {uid}: {repr(e)}")
        print(f"Error type: {type(e)}")
        verdict['status'] = 'exception'
        return {'tests': [], 'error': f'exception: {type(e)} {e}'}, verdict
    
    finally:
        try:
            shutil.rmtree(workspace_dir, ignore_errors=True)
        except Exception:
            pass


def main():
    raw_sample_df = pd.read_parquet('swebench-pro.parquet')
    raw_sample_df = raw_sample_df.fillna("")
    raw_sample_df = raw_sample_df.set_index("instance_id", drop=False)

    valid_patches = []

    guess_instance_id_map = {}
    for _, row in raw_sample_df.iterrows():
        guess_instance_id_map[(
            row['repo'],
            row['problem_statement'],
            row['requirements'],
            row['interface'],
        )] = row['instance_id']

    assert RESULT_PATH.is_dir()
    for p in RESULT_PATH.glob('*/p*i*'):
        try:
            with (p / '_harness' / 'verdict_gen.json').open() as f:
                verdict_gen = json.load(f)
                instance_id = verdict_gen['instance_id']
                assert instance_id in raw_sample_df.index
        except FileNotFoundError:
            # the result is generated by an old version, try to guess the instance_id by input
            with (p / 'instance.json').open() as f:
                info = json.load(f)
                key = (
                    info['repo'],
                    info['problem_statement'],
                    info['requirements'],
                    info['interface'],
                )
            instance_id = guess_instance_id_map[key]

        try:
            with (p / 'patch.diff').open() as f:
                patch = f.read()
        except FileNotFoundError:
            patch = None

        valid_patches.append({
            'instance_id': instance_id,
            'patch': patch,
            'base_path': p,
            'approach_name': p.parent.name,
        })

    del guess_instance_id_map

    resolved_counter = Counter()
    total_counter = Counter()

    print(f'=== begin validation of {len(valid_patches)} patches ===')

    # Use ThreadPoolExecutor to run evaluations in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=EVAL_THREADS) as executor:
        # Create a dictionary mapping futures to their patch samples for progress tracking
        future_to_patch = {
            executor.submit(
                eval_with_docker,
                patch_sample['patch'],
                raw_sample_df.loc[patch_sample['instance_id']],
                str(patch_sample['base_path'] / '_harness' / 'eval'),
                prefix='sweap',
                redo=True,
                block_network=False,
            ): patch_sample
            for patch_sample in valid_patches
        }

        # Track progress with tqdm and show running accuracy
        pbar = tqdm(concurrent.futures.as_completed(future_to_patch), total=len(valid_patches))
        for future in pbar:
            patch_sample = future_to_patch[future]
            verdict = {}
            try:
                # Get the result (if any error occurred, it will be raised here)
                output, verdict = future.result()
                raw_sample = raw_sample_df.loc[patch_sample["instance_id"]]
                passed_tests = {x["name"] for x in output["tests"] if x["status"] == "PASSED"}
                f2p = set(eval(raw_sample["fail_to_pass"]))
                p2p = set(eval(raw_sample["pass_to_pass"]))
                resolved = (f2p | p2p) <= passed_tests

                verdict['resolved'] = resolved
                verdict['passed_tests'] = list(passed_tests)
                verdict['all_tests'] = list(f2p | p2p)
                print(f'{patch_sample["base_path"]}: resolved = {resolved}, status = {verdict.get("status", "--")}')

                resolved_counter[patch_sample['approach_name']] += int(resolved)
                total_counter[patch_sample['approach_name']] += 1

            except Exception as exc:
                print(f'Evaluation for {patch_sample["instance_id"]} generated an exception: {exc}')
                verdict['status'] = 'exception'
                verdict['resolved'] = False
                verdict['exception'] = f'{type(exc)} {exc}'
            finally:
                verdict_p = patch_sample['base_path'] / '_harness' / 'verdict_val.json'
                with verdict_p.open('w') as f:
                    json.dump(verdict, f, indent=4)
                os.chown(verdict_p, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)

    print('=== validation results ===')
    for k in total_counter.keys():
        print(f'{k}: {resolved_counter.get(k, 0)} / {total_counter[k]}')

if __name__ == "__main__":
    main()