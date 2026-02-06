import argparse
import yaml
import json
from minisweagent.models.litellm_model import LitellmModel

from env import SshEnvironment
from agent import MemoryAgent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance-path', required=True)
    parser.add_argument('--memory-path', required=True)
    parser.add_argument('--llm-base-url', required=True)
    parser.add_argument('--llm-api-key', required=True)
    parser.add_argument('--env-ssh', required=True)
    args = parser.parse_args()

    with open(f'{args.instance_path}/instance.json') as f:
        instance = json.load(f)
    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    ssh_user_password, _, ssh_host = args.env_ssh.rpartition('@')
    ssh_user, _, ssh_password = ssh_user_password.partition(':')

    task = instance['problem_statement']
    if instance['requirements']:
        task += f'\n\nRequirements:\n{instance["requirements"]}'
    if instance['interface']:
        task += f'\n\nNew interfaces introduced:\n{instance["interface"]}'
    task += f'\n\nThe {instance["repo_language"]} project "{instance["repo"]}" has been cloned to {config["environment"]["cwd"]}'

    print('agent started!')

    agent = MemoryAgent(
        args.memory_path,
        LitellmModel(
            model_name=config['model']['model_name'],
            model_kwargs={
                'api_base': args.llm_base_url,
                'api_key': args.llm_api_key,
                **config['model'].get('model_kwargs', {}),
            },
            cost_tracking='ignore_errors',
        ),
        SshEnvironment(
            ssh_host=ssh_host,
            ssh_port=22,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            cwd=config['environment']['cwd'],
            env=config['environment']['env'],
            timeout=config['environment']['timeout'],
        ),
        system_template=config['agent']['system_template'],
        instance_template=config['agent']['instance_template'],
        timeout_template=config['agent']['timeout_template'],
        format_error_template=config['agent']['format_error_template'],
        action_observation_template=config['agent']['action_observation_template'],
        step_limit=config['agent']['step_limit'],
        cost_limit=config['agent']['cost_limit'],
    )
    status, result = agent.run(task)
    agent.save_memory()
    
    print('done! status:', status)
    if status == 'Submitted':
        with open(f'{args.instance_path}/patch.diff', 'w') as f:
            f.write(result)
    else:
        print(result)

if __name__ == "__main__":
    # forward SIGTERM from `docker stop` to Python exception
    import signal
    import sys
    signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(-sig))

    main()