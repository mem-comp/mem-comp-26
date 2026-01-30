from litellm.integrations.custom_logger import CustomLogger
from pathlib import Path
import json
import random
import traceback

class TrajLogger(CustomLogger):
    '''
    Trajectory Logger for LiteLLM
    '''

    def __init__(self):
        super().__init__()
        self._traj_base_path = Path('/mnt/trajs/')

    def _log_traj(self, kwargs):
        out_path = None
        try:
            log_obj = kwargs['standard_logging_object']
            keyhash = log_obj['metadata']['user_api_key_hash']
            start_time = log_obj['startTime']

            serial = f'{start_time*1000:.0f}_{random.randint(0, 1000000):06d}'
            out_path = self._traj_base_path / keyhash[:20] / f'{serial}.json'
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with out_path.open('w') as f:
                json.dump({
                    'status': log_obj.get('status', None),
                    'keyhash': keyhash,
                    'user_id': log_obj['metadata'].get('user_api_key_user_id', None),
                    'start_time': start_time,
                    'end_time': log_obj.get('endTime', None),
                    'call_type': log_obj.get('call_type', None),
                    'model': kwargs.get('model', None),
                    'usage_object': log_obj['metadata'].get('usage_object', None),
                    'cost_breakdown': log_obj.get('cost_breakdown', None),
                    'model_parameters': log_obj.get('model_parameters', None),
                    'instructions': kwargs.get('instructions', None),
                    'messages': log_obj.get('messages', None),
                    'error_information': log_obj.get('error_information', None),
                    'response': log_obj.get('response', None),
                    'response_headers': (kwargs.get('litellm_params', {}).get('metadata', {}) or {}).get('hidden_params', {}).get('additional_headers', None),
                    #'raw_kwargs': kwargs,
                }, f, indent=2, ensure_ascii=False, default=lambda o: f'<not serializable: {type(o)}>')

        except Exception as e:
            tb = traceback.format_exc()
            print(f'!!! FAILED to log traj: {type(e)} {e}\n{tb}')
            print(kwargs)
            print('=== end ===')
            if out_path:
                with out_path.open('w') as f:
                    json.dump({
                        'status': 'exception',
                        'exception_type': type(e).__name__,
                        'exception_message': str(e),
                        'traceback': tb,
                    }, f, indent=2, ensure_ascii=False, default=lambda o: f'<not serializable: {type(o)}>')

    # upstream loggers commented out
    '''
    def log_pre_api_call(self, model, messages, kwargs): 
        print('=== log_pre_api_call', model)
        print(kwargs)
        print(messages)
    
    def log_post_api_call(self, kwargs, response_obj, start_time, end_time): 
        print('=== log_post_api_call', start_time, end_time)
        print(kwargs)
        print(response_obj)
        
    def log_stream_event(self, kwargs, response_obj, start_time, end_time):
        print('=== log_stream_event', start_time, end_time)
        print(kwargs)
        print(response_obj)

    async def async_log_pre_api_call(self, model, messages, kwargs):
        print('= async')
        self.log_pre_api_call(model, messages, kwargs)

    async def async_log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        print('= async')
        self.log_post_api_call(kwargs, response_obj, start_time, end_time)

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        print('= async')
        self.log_stream_event(kwargs, response_obj, start_time, end_time)
    '''

    def log_success_event(self, kwargs, response_obj, start_time, end_time): 
        self._log_traj(kwargs)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._log_traj(kwargs)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._log_traj(kwargs)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time): 
        self._log_traj(kwargs)

traj_logger_instance = TrajLogger()