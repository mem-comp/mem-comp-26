from pydantic import BaseModel
import paramiko
import shlex
import traceback
from typing import Any

class SshEnvironmentConfig(BaseModel):
    ssh_host: str = ''
    ssh_port: int = 22
    ssh_user: str = ''
    ssh_password: str = ''
    cwd: str = ''
    env: dict[str, str] = {}
    timeout: int = 180

class SshEnvironment:
    def __init__(self, *, config_class: type = SshEnvironmentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self._client: paramiko.SSHClient | None = None

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        if self._client is None:
            return

        try:
            self._client.close()
        except Exception:
            return
        self._client = None

    def _ensure_client(self) -> paramiko.SSHClient:
        if self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return self._client
            
            self.close()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.config.ssh_host,
            port=self.config.ssh_port,
            username=self.config.ssh_user,
            password=self.config.ssh_password or None,
        )
        self._client = client
        return client

    def execute(self, command: str, cwd: str = '', *, timeout: int | None = None):
        print('exec command:', command.replace('\n', ' \\n ')[:80])

        cwd = cwd or self.config.cwd
        timeout = timeout or self.config.timeout
        if cwd:
            command = f'cd {shlex.quote(cwd)} ; {command}'
        
        try:
            client = self._ensure_client()
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout, environment=self.config.env)
            stdin.close()

            output = stdout.read().decode('utf-8', errors='replace').strip()
            output_stderr = stderr.read().decode('utf-8', errors='replace').strip()
            if output_stderr:
                output = f'[stdout]\n{output}\n\n[stderr]\n{output_stderr}'

            return {'output': output, 'returncode': stdout.channel.recv_exit_status()}
        
        except Exception as e:
            traceback.print_exc()
            self.close()
            return {'output': f'Failed to execute ({type(e)}): {e}', 'returncode': -1}

    def get_template_vars(self) -> dict[str, Any]:
        return self.config.model_dump()