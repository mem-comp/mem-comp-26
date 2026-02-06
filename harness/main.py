from dataclasses import dataclass, asdict
import pandas as pd
from pathlib import Path
import shutil
import psutil
import random
import json
from contextlib import contextmanager
import os
import docker
import threading
import multiprocessing
import secrets
import requests
import datetime
import traceback
import time
from pwd import getpwnam
from dotenv import load_dotenv
from typing import Callable

load_dotenv()

if os.geteuid() != 0:
    raise PermissionError('please run as root!')
    # because file operations may fail for folders mounted to docker

VERBOSE = int(os.getenv('VERBOSE', '2'))
FILE_OWNER_UID = getpwnam(os.getenv('FILE_OWNER', 'root')).pw_uid
RESULT_PATH = Path('results')
WORK_PATH = Path('workdir')
SSHBOX_SCRIPT_PATH = Path('../sshbox_scripts')
LITELLM_TRAJ_PATH = Path(os.getenv('LITELLM_TRAJ_DIR', '../play/trajs'))
LITELLM_BASEURL_IN_DOCKER = 'http://litellm_app:4000'
LITELLM_CONTROLLER_BASEURL = 'http://127.0.0.1:4001'

ROOTFS_DEVICE = os.getenv('ROOTFS_DEVICE', '/dev/sda')
DOCKER_LIMITS = {
    'cpu_period': 100000,
    'cpu_quota': 100000 * 6,
    'mem_limit': '12g',
    'memswap_limit': '12g',
    'pids_limit': 32768,
    'blkio_weight': 200,
    'device_read_bps': [{'Path': ROOTFS_DEVICE, 'Rate': 30*1024*1024}],
    'device_write_bps': [{'Path': ROOTFS_DEVICE, 'Rate': 30*1024*1024}],
    'device_read_iops': [{'Path': ROOTFS_DEVICE, 'Rate': 2000}],
    'device_write_iops': [{'Path': ROOTFS_DEVICE, 'Rate': 2000}],
    'dns': ['223.5.5.5'],
    'environment': ['TZ=Asia/Shanghai'],
}

### BEGIN copied from sweap

def sweap_get_docker_tag(row):
    uid = row['instance_id']
    repo_name = row.get('repo', '')
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = uid.replace("instance_", "")

    if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = 'element-web'  # Keep full name for this one case
    elif 'element-hq' in repo_name.lower() and 'element-web' in repo_name.lower():
        repo_name_only = 'element'
        if hsh.endswith('-vnan'):
            hsh = hsh[:-5]
    # All other repos: strip -vnan suffix
    elif hsh.endswith('-vnan'):
        hsh = hsh[:-5]

    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]
    return tag

### END copied from sweap

def ts():
    return datetime.datetime.now().isoformat()

sweap_df = pd.read_csv('external_hf_v2.csv', engine='c', on_bad_lines='skip')
sweap_df = sweap_df.set_index('instance_id', drop=False)

class Instance:
    DOCKER_IMAGE_BASE: str = os.environ.get('DOCKER_IMAGE_BASE', 'jefzda/sweap-images')

    def __init__(self, instance_id: str, ident: int):
        self.instance_id: str = instance_id
        self.ident: str = ident # used as llm key name
        self.instance: pd.Series = sweap_df.loc[instance_id]
        self.env_docker_image: str = f'{self.DOCKER_IMAGE_BASE}:{sweap_get_docker_tag(self.instance)}'
        self.instance_input: dict[str, str] = {
            k: self.instance[k]
            for k in ['repo', 'repo_language', 'problem_statement', 'requirements', 'interface']
        }

@dataclass
class Candidate:
    run_name: str # used as llm user name and output folder name
    agent_docker_image: str
    llm_quota_total: float
    llm_quota_instance: float
    enable_memory: bool
    timeout_s: float

class Workdir:
    def __init__(self, stem: str, cleanup_fn: Callable[['Workdir'], None] | None = None):
        self.name: str = f'{stem}--{random.randint(0, 1000000)}'
        self.path: Path = WORK_PATH / self.name
        self.cleanup_fn: Callable[['Workdir'], None] | None = cleanup_fn

        self.path.mkdir(parents=True, exist_ok=True)

        if VERBOSE >= 1:
            print(f'{ts()} | create workdir {self.name}')

    def cleanup(self):
        if self.path:
            if VERBOSE >= 1:
                print(f'{ts()} | cleanup workdir {self.name}')

            if self.cleanup_fn:
                self.cleanup_fn(self)
            
            if self.path.exists():
                shutil.rmtree(self.path, ignore_errors=True)

            self.path = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        
    def __del__(self):
        self.cleanup()
        
docker_client = docker.from_env()

def eval_single_project(instances: list[Instance], candidate: Candidate):
    assert len(instances) > 0
    mem_dir = Workdir(f'{candidate.run_name}-mem-global-{instances[0].ident}')

    @contextmanager
    def llm_key_scope(key_alias):
        _create_key_resp = requests.get(
            f'{LITELLM_CONTROLLER_BASEURL}/harness/create_key',
            params={'user_id': candidate.run_name, 'key_alias': key_alias, 'quota': candidate.llm_quota_instance},
        ).json()
        llm_key = _create_key_resp['key']
        llm_keyhash = _create_key_resp['hash']
        try: 
            yield llm_key, llm_keyhash
        finally:
            if VERBOSE >= 1:
                print(f'{ts()} | cleanup llm key {llm_key} ({llm_keyhash})')
            
            requests.get(
                f'{LITELLM_CONTROLLER_BASEURL}/harness/delete_key',
                params={'key': llm_key},
            )

    @contextmanager
    def sshbox_scope(instance: Instance, pcap_path: Path):
        ssh_password = secrets.token_hex(10)
        cont = docker_client.containers.create(
            instance.env_docker_image,
            [ssh_password],
            entrypoint=['/mnt/sshbox/start.sh'],
            name=f'memcomp-{candidate.run_name}-{instance.ident}-sshbox',
            detach=True,
            restart_policy={'Name': 'no'},
            network='infra',
            volumes={
                str(SSHBOX_SCRIPT_PATH.resolve()): {'bind': '/mnt/sshbox', 'mode': 'ro'},
                str(pcap_path.resolve()): {'bind': '/mnt/pcap', 'mode': 'rw'},
            },
            **DOCKER_LIMITS,
        )
        docker_client.networks.get('internet').connect(cont)
        
        try:
            cont.start()
            
            cont.reload()
            sshbox_ip = cont.attrs['NetworkSettings']['Networks']['infra']['IPAddress']
            assert sshbox_ip
            sshbox_conn_str = f'root:{ssh_password}@{sshbox_ip}'

            yield sshbox_conn_str
        finally:
            if VERBOSE >= 1:
                print(f'{ts()} | cleanup sshbox {cont.name}')
            
            try:
                cont.stop(timeout=3)
                cont.remove(force=True)
            except Exception:
                if VERBOSE >= 1:
                    print(f'{ts()} | cleanup sshbox {cont.name} failed, try again')
                
                try:
                    cont.remove(force=True)
                except Exception:
                    if VERBOSE >= 1:
                        print(f'{ts()} | cleanup sshbox {cont.name} failed, ignoring')

    @contextmanager
    def agent_scope(inst_path: Path, mem_path: Path, llm_key: str, ssh_conn_str: str, log_path: Path):
        cont = docker_client.containers.create(
            candidate.agent_docker_image,
            [
                '--instance-path', '/mnt/instance',
                '--memory-path', '/mnt/memory',
                '--llm-base-url', LITELLM_BASEURL_IN_DOCKER,
                '--llm-api-key', llm_key,
                '--env-ssh', ssh_conn_str,
            ],
            name=f'memcomp-{candidate.run_name}-{instance.ident}-agent',
            detach=True,
            restart_policy={'Name': 'no'},
            network='infra',
            volumes={
                str(inst_path.resolve()): {'bind': '/mnt/instance', 'mode': 'rw'},
                str(mem_path.resolve()): {'bind': '/mnt/memory', 'mode': 'rw'},
            },
            **DOCKER_LIMITS,
        )
        
        def log_thread():
            with log_path.open('wb') as logf:
                for content in cont.logs(stream=True, timestamps=True, follow=True):
                    logf.write(content)
                    logf.flush()

                    if VERBOSE >= 2:
                        t = ts()
                        for line in content.decode().splitlines():
                            line = line.partition(' ')[2] # remove timestamp prefix
                            print(f'{t} | {candidate.run_name} {instance.ident}: > {line}')

        try:
            cont.start()

            time.sleep(1) # xxx: must start log_thread after container is started to properly receive logs
            log_thread = threading.Thread(target=log_thread, daemon=True)
            log_thread.start()

            yield cont
        finally:
            if VERBOSE >= 1:
                print(f'{ts()} | cleanup agent {cont.name} ({candidate.agent_docker_image})')

            try:
                cont.stop(timeout=3)
                cont.remove(force=True)
            except Exception:
                if VERBOSE >= 1:
                    print(f'{ts()} | cleanup agent {cont.name} ({candidate.agent_docker_image}) failed, try again')

                try:
                    cont.remove(force=True)
                except Exception:
                    if VERBOSE >= 1:
                        print(f'{ts()} | cleanup agent {cont.name} ({candidate.agent_docker_image}) failed, ignoring')
            log_thread.join()

    try:
        for instance in instances:
            print(f'{ts()} | {candidate.run_name} {instance.ident}: INIT')

            # create memory path
            if not candidate.enable_memory:
                mem_dir.cleanup()
                mem_dir = Workdir(f'{candidate.run_name}-{instance.ident}-mem')

            # create instance path
            inst_path = RESULT_PATH / candidate.run_name / instance.ident
            if inst_path.exists():
                shutil.rmtree(inst_path, ignore_errors=True)
            inst_path.mkdir(parents=True)

            def perform_cleanup(log_dir: Workdir):
                # collect memory snapshot

                shutil.copytree(mem_dir.path, log_dir.path / 'memory')

                # move log_dir to results before it is cleaned up

                log_dest = inst_path / '_harness'
                if log_dest.is_dir():
                    shutil.rmtree(log_dest, ignore_errors=True)
                elif log_dest.is_file():
                    log_dest.unlink()
                
                shutil.move(log_dir.path, log_dest)

                # fix permission

                for dirpath, dirnames, filenames in os.walk(inst_path):
                    os.chown(dirpath, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)
                    os.chmod(dirpath, 0o755, follow_symlinks=False)
                    for filename in filenames:
                        fn = os.path.join(dirpath, filename)
                        os.chown(fn, uid=FILE_OWNER_UID, gid=FILE_OWNER_UID, follow_symlinks=False)
                        os.chmod(fn, 0o644, follow_symlinks=False)

            # create log path
            with Workdir(f'{candidate.run_name}-{instance.ident}-log', cleanup_fn=perform_cleanup) as log_dir:
                llm_keyhash = '???'

                with (
                    # create llm key and sshbox
                    llm_key_scope(instance.ident) as (llm_key, llm_keyhash_),
                    sshbox_scope(instance, log_dir.path / 'pcap') as sshbox_conn_str,
                    # create harness system log file
                    (log_dir.path / 'system.log').open('w') as logf,
                ):
                    llm_keyhash = llm_keyhash_

                    logf.write(f'candidate: {asdict(candidate)}\n')
                    logf.write(f'instance: {instance.ident}, instance_id = {instance.instance_id}\n')
                    logf.write(f'llm key: {llm_key}, hash = {llm_keyhash}\n')
                    logf.write(f'sshbox: {sshbox_conn_str}\n')

                    if VERBOSE >= 1:
                        print(f'{ts()} | {candidate.run_name} {instance.ident}: key = {llm_key} ; hash = {llm_keyhash} ; ssh = {sshbox_conn_str}')
                    
                    # write input

                    with (inst_path / 'instance.json').open('w') as f:
                        json.dump(instance.instance_input, f, indent=4)

                    # run candidate agent

                    time.sleep(1)

                    logf.write(f'load: {psutil.getloadavg()} with {psutil.cpu_count()} cores\ncpu: {psutil.cpu_times_percent()}\nmem: {psutil.virtual_memory()}\ndisk: {psutil.disk_usage(".")}\n')

                    print(f'{ts()} | {candidate.run_name} {instance.ident}: START')
                    logf.write(f'start running agent @ {ts()}\n')
                    
                    with agent_scope(inst_path, mem_dir.path, llm_key, sshbox_conn_str, log_dir.path / 'agent.log') as cont:
                        try:
                            agent_ret = cont.wait(timeout=candidate.timeout_s)
                        except Exception as e:
                            if VERBOSE >= 1:
                                print(f'{ts()} | {candidate.run_name} {instance.ident}: agent timeout: {repr(e)}')
                            
                            logf.write(f'agent timeout:\n{traceback.format_exc()}\n')
                        else:
                            if VERBOSE >= 1:
                                print(f'{ts()} | {candidate.run_name} {instance.ident}: agent ret = {agent_ret}')

                            logf.write(f'agent finished: retcode = {agent_ret["StatusCode"]}\n')

                        logf.write(f'stop running agent @ {ts()}\n')

                    print(f'{ts()} | {candidate.run_name} {instance.ident}: FIN')
                    logf.write(f'agent removed @ {ts()}\n')

                    # query llm usage

                    usage = requests.get(
                        f'{LITELLM_CONTROLLER_BASEURL}/harness/query_balance',
                        params={'user_id': candidate.run_name, 'key': llm_key},
                    ).json()

                    if VERBOSE >= 1:
                        print(f'{ts()} | {candidate.run_name} {instance.ident}: llm usage = {usage}')

                    logf.write(f'llm usage: {usage}\n')

                # when the context manager exits: llm key will be deleted, sshbox will be removed, logf will be flushed

                time.sleep(1)

                # collect litellm traj

                traj_src = LITELLM_TRAJ_PATH / llm_keyhash
                traj_dest = log_dir.path / 'traj'
                
                if traj_src.is_dir():
                    shutil.move(traj_src, traj_dest)
                else:
                    if VERBOSE >= 1:
                        print(f'{ts()} | {candidate.run_name} {instance.ident}: traj dir not found')

            # when the context manager exits: log_dir will be cleaned up

        print(f'{ts()} | {candidate.run_name}: DONE')            

    except Exception:
        print(f'{ts()} | {candidate.run_name}: FATAL ERROR')
        traceback.print_exc()

    finally:
        mem_dir.cleanup()

def eval_candidate(projects: list[list[str]], candidate: Candidate):
    @contextmanager
    def llm_user_scope():
        res = requests.get(
            f'{LITELLM_CONTROLLER_BASEURL}/harness/create_user',
            params={'user_id': candidate.run_name, 'user_alias': candidate.run_name, 'quota': candidate.llm_quota_total},
        )
        res.raise_for_status()
        try: 
            yield
        finally:
            if VERBOSE >= 1:
                print(f'{ts()} | cleanup llm user {candidate.run_name}')
            
            res = requests.get(
                f'{LITELLM_CONTROLLER_BASEURL}/harness/delete_user',
                params={'user_id': candidate.run_name},
            )
            res.raise_for_status()

    # create llm user
    with llm_user_scope():
        workers = []

        # load instances
        for pidx, project in enumerate(projects):
            instances = []
            for iidx, instance_id in enumerate(project):
                instances.append(Instance(instance_id, f'p{pidx:02d}i{iidx:02d}'))
        
            # start eval process (not thread because finally statements in threads are broken)
            p = multiprocessing.Process(target=eval_single_project, args=(instances, candidate))
            workers.append(p)
            p.start()
        
        # wait for eval to finish
        for worker in workers:
            worker.join()

    # when the context manager exits: llm user will be deleted

if __name__ == '__main__':
    with open('projects.json') as f:
        projects = json.load(f)
    with open('candidates.json') as f:
        candidates = json.load(f)

    print(f'loaded candidates = {len(candidates)} ; instances = {sum(len(p) for p in projects)} = {[len(p) for p in projects]}')

    def san_check():
        # check path

        assert SSHBOX_SCRIPT_PATH.exists()
        assert LITELLM_TRAJ_PATH.exists()
        RESULT_PATH.mkdir(parents=True, exist_ok=True)
        WORK_PATH.mkdir(parents=True, exist_ok=True)

        paths_to_delete = []
        for candidate in candidates:
            p = RESULT_PATH / candidate['run_name']
            if p.exists():
                paths_to_delete.append(p)

        if paths_to_delete:
            for p in paths_to_delete:
                print(f'- {p}')
            print(f'WARNING: will cleanup {len(paths_to_delete)} existing results in 10 seconds, CTRL+C NOW to abort')
            time.sleep(10)

            for path in paths_to_delete:
                shutil.rmtree(path, ignore_errors=True)

        # check url

        res = requests.get(f'{LITELLM_CONTROLLER_BASEURL}/harness/health')
        res.raise_for_status()
        assert 'v1' in res.json()['compat']

        cont = docker_client.containers.run(
            'curlimages/curl:8.6.0',
            ['-sS', '-m', '5', '-o', '/dev/null', '-w', '%{http_code}', f'{LITELLM_BASEURL_IN_DOCKER}/health/liveliness'],
            detach=True,
            network='infra',
            **DOCKER_LIMITS,
        )
        cont.wait()
        log = cont.logs().decode(errors='replace')
        cont.remove(force=True)
        assert log.strip() == '200'
        
        # check docker image
        
        images = []
        for c in candidates:
            images.append(c['agent_docker_image'])
        for p in projects:
            for i in p:
                images.append(f'{Instance.DOCKER_IMAGE_BASE}:{sweap_get_docker_tag(sweap_df.loc[i])}')
        
        for image in images:
            try:
                docker_client.images.get(image)
            except docker.errors.ImageNotFound:
                print(f'pulling {image}')
                docker_client.images.pull(image)
    
    san_check()

    print(f'{ts()} | begin')
    for c in candidates:
        eval_candidate(projects, Candidate(**c))
    print(f'{ts()} | done')