# Competition of Intelligent Coding Assistant Enhanced with Memory

**Links: [Competition website](https://mem-comp.github.io/) / [HotCRP](https://mem-comp26.hotcrp.com/) / [Discord](https://discord.gg/eH6DN82k4u)**

This repository contains a local evaluation harness for the competition. It also includes a modified [mini-swe-agent](https://github.com/SWE-agent/mini-SWE-agent) that stores the trajectory of the finished task and prepends it to the user prompt of the next task. This is an EXTREMELY NAIVE memory implementation, but it can serve as an example for you to understand the harness and get started.

*Details are subject to change. If you have concerns or suggestions, feel free to discuss them with the organizers in the Discord channel.*

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

`--instance-path` is a path to the task instance. Your agent reads the task description in `instance.json` ([example](play/instance/instance.json)) and outputs the patch to `patch.diff` in this directory. It can also output debug information by writing to other files in this directory.

`--memory-path` is where your agent stores and reads the memory. We will invoke your agent sequentially for each task in the same project, and this is the only directory that will be preserved between different tasks. It will be empty at the beginning, and your agent can store memory in any format.

`--llm-base-url` specifies the base URL for an OpenAI-compatible LLM service (based on [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/docker_quick_start)). Your agent can only use LLMs provided by this service with the given API key (`--llm-api-key`). This service will log all requests from your agent, and limit the quota for each task.

`--env-ssh` points to an SSH environment for the task. Your agent can access the cloned project at `/app` and execute commands in this environment. Note that unlike traditional SWE agents that spawn Docker containers themselves, your agent should instead use the SSH environment. The SSH environment will log all network traffic, including executed commands.

## Getting Started

To set up the evaluation harness and test your agent with the default `instance_NodeBB__NodeBB-51d8f3b195bddb13a13ddc0de110722774d9bb1b-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e` task:

1. Use an x86-64 Linux machine with Docker installed
2. Clone this repository: `git clone https://github.com/mem-comp/mem-comp-26`
3. Edit `litellm/config_example.yaml` to add LLMs your agent will use ([docs](https://docs.litellm.ai/docs/providers/))
4. Run `sudo ./init.sh` to start the LLM service in background and create a test key
5. Edit `agent_example/docker-compose.yaml` to fill in the key after `--llm-api-key`
6. Run `docker compose up --build` in the `agent_example` directory to start your agent
7. The output and logs will be stored in the `play` directory

To test with another task, you can:

1. Edit `play/instance/instance.json` to change the task description, and delete other files in the `play/instance` directory
2. In `agent_example/docker-compose.yaml`, edit the `image` of `sshbox` to the new instance
3. Run `curl http://127.0.0.1:4001/test_key/reset` to get a new key with quota reset, and also update the `--llm-api-key` in `agent_example/docker-compose.yaml` to the new key
4. Run `docker compose up --build` in the `agent_example` directory to start your agent