# Competition of Intelligent Coding Assistant Enhanced with Memory

**Links: [Competition website](https://mem-comp.github.io/) / [HotCRP](https://mem-comp26.hotcrp.com/) / [Discord](https://discord.gg/eH6DN82k4u)**

This repository contains a local evaluation harness for the competition. It also includes a modified [mini-swe-agent](https://github.com/SWE-agent/mini-SWE-agent) that stores the trajectory of the finished task and prepends it to the user prompt of the next task. This is an EXTREMELY NAIVE memory implementation, but it can serve as an example for you to understand the harness and get started.

## Input & Output Format

Your task is to implement a memory-enhanced SWE agent system as a Docker image. The memory component may be implemented using any suitable approach. We will invoke your agent for each task with a Docker command like this:

```bash
docker run --rm
  -v "...:/mnt/instance"
  -v "...:/mnt/memory"
  YOUR_AGENT_IMAGE
  --instance-path /mnt/instance
  --memory-path /mnt/memory
  --llm-base-url http://litellm_app:4000
  --llm-api-key sk-...
  --env-ssh root:password@sshbox
```

In the arguments to your agent:

`--instance-path` points to a directory for the task instance. Your agent reads the task description in `instance.json` ([example](play/instance/instance.json)) and outputs the patch to `patch.diff` in this directory. It can also output debug information by writing to other files in this directory. Files in this directory won't preserve between tasks.

`--memory-path` points to a directory where your agent stores and reads the memory. We will invoke your agent sequentially for each task in the same project, and files in this directory will be preserved between tasks in a project (but not between projects). The directory will be empty at the beginning, and your agent can store memory in any format.

`--llm-base-url` specifies the base URL for an OpenAI-compatible LLM service (based on [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/docker_quick_start)). Your agent can only use LLMs provided by this service with the given API key (`--llm-api-key`). This service will log all requests from your agent, and limit the quota for each task.

`--env-ssh` points to an SSH environment for the task. Your agent can access the project cloned at `/app` and execute commands in this environment. Note that unlike traditional SWE agents that spawn Docker containers themselves, your agent should instead use the SSH environment. The SSH environment will log all network traffic, including the executed commands.

## Harness Usage

### Setup and Manual Evaluation

You will need an x86-64 Linux machine with Docker installed for running the evaluation harness.

To set up the evaluation harness and test your agent with the default task (`instance_NodeBB__NodeBB-51d8f3b195bddb13a13ddc0de110722774d9bb1b-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e`):

1. Clone this repository:
   - Run `git clone https://github.com/mem-comp/mem-comp-26 && cd mem-comp-26`
2. Configure and start the LLM service:
   - Edit `litellm/config_example.yaml` to add the LLM your agent will use ([docs](https://docs.litellm.ai/docs/providers/))
   - Run `sudo ./init.sh` to start the LLM service in background and create a test key
3. Configure your agent (using the provided mini-swe-agent as an example):
   - Edit `agent_example/docker-compose.yaml` to fill in your test key after `--llm-api-key`
   - Edit `agent_example/src/config.yaml` to fill in the model name (as configured in LiteLLM) after `model_name:`
4. Start evaluation:
   - Run `docker compose up --build` in the `agent_example` directory to start your agent
   - The patch and logs will be stored in the `play` directory
   - If you want to judge the correctness of the patch, refer to [SWE-Bench-Pro](https://github.com/scaleapi/SWE-bench_Pro-os)

To test with another task manually, you can:

1. Update the task input:
   - Edit `play/instance/instance.json` to change the task description
   - Delete other files in the `play/instance` directory
   - In `agent_example/docker-compose.yaml`, edit the `image` of `sshbox` to the new instance
2. Reset LLM quota:
   - Run `curl http://127.0.0.1:4001/test_key/reset` to get a new key with quota reset
   - Update the `--llm-api-key` in `agent_example/docker-compose.yaml` to the new key
3. Start evaluation

### Automated Evaluation

To run an automated batch evaluation, you can use our harness script:

1. Edit `harness/candidates.json` to add your agent
2. Edit `harness/projects.json` to list the tasks to evaluate with
3. Run `cd harness; python3 -m pip install -r requirements.txt`
4. Run `sudo -E python3 main.py` in the `harness` directory to start the evaluation
5. The results will be stored in the `results` directory

### Submission

You should make the Docker image of your agent available to the reviewers. A common approach is to [publish it on DockerHub](https://docs.docker.com/get-started/docker-concepts/building-images/build-tag-and-publish-an-image/).

The `agent_example`  directory builds the agent with the tag `minisweagent-withmem` by default. You can publish the built image with commands like these:

```bash
docker login
docker image tag minisweagent-withmem YOUR_DOCKER_USERNAME_HERE/APPROACH_NAME_HERE:v1
docker push YOUR_DOCKER_USERNAME_HERE/APPROACH_NAME_HERE:v1
```

The `docker push` command will output the digest of your image  (`digest: sha256:...`). Please provide your full image name, tag, and digest (e.g., `YOUR_DOCKER_USERNAME_HERE/APPROACH_NAME_HERE:v1@sha256:...`) in the "System Submission" field on HotCRP.
