import os
import json
import time
import requests
from minisweagent.agents.default import DefaultAgent, LimitsExceeded

class MemoryAgent(DefaultAgent):
    def __init__(self, memory_path: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.memory_path = f'{memory_path}/memory.json'
        self.memorized_messages = []
        self.load_memory()

    def load_memory(self) -> None:
        if os.path.exists(self.memory_path):
            with open(self.memory_path, 'r') as f:
                self.memorized_messages = [
                    *json.load(f),
                    {
                        'role': 'user',
                        'content': '**A new task started. The project root path has been reset to a clean state for this task.**',
                        'timestamp': time.time(),
                    },
                ]
            print(f'loaded {len(self.memorized_messages)} memorized messages')

    def save_memory(self) -> None:
        print(f'saved {len(self.messages[1:])} messages to memory')
        with open(self.memory_path, 'w') as f:
            json.dump(self.messages[1:], f, indent=2)

    def print_spend(self) -> None:
        u = self.model.config.model_kwargs['api_base']
        h = {'Authorization': f'Bearer {self.model.config.model_kwargs["api_key"]}'}

        try:
            info = requests.get(f'{u}/user/info', headers=h, timeout=2).json()
            user_spend = f'{info["user_info"]["spend"]:.4f} / {info["user_info"]["max_budget"]:.4f}'
        except Exception:
            user_spend = '???'

        try:
            info = requests.get(f'{u}/key/info', headers=h, timeout=2).json()
            key_spend = f'{info["info"]["spend"]:.4f} / {info["info"]["max_budget"]:.4f}'
        except Exception:
            key_spend = '???'

        print(f'spend so far: key {key_spend}, user {user_spend}')

    def query(self) -> dict:
        """Query the model and return the response."""

        if 0 < self.config.step_limit <= self.model.n_calls or 0 < self.config.cost_limit <= self.model.cost:
            raise LimitsExceeded()
        
        self.print_spend()
        print(f'query llm: step {self.model.n_calls}')
        
        # insert memorized messages after the first message (system prompt)
        messages = [
            *self.messages[:1],
            *self.memorized_messages,
            *self.messages[1:]
        ]

        response = self.model.query(messages)
        self.add_message('assistant', **response)
        return response