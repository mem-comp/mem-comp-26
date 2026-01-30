from sanic import Sanic, text, json
import os
import httpx
import hashlib

LITELLM_BASEURL = os.getenv('LITELLM_BASEURL')
LITELLM_KEY = os.getenv('LITELLM_KEY')
GLOBAL_BUDGET = float(os.getenv('GLOBAL_BUDGET'))
INSTANCE_BUDGET = float(os.getenv('INSTANCE_BUDGET'))

SAFE_ROUTES = [
    # openai
    '/models', '/v1/models',
    '/chat/completions', '/v1/chat/completions',
    '/responses', '/v1/responses',
    '/rerank', '/v1/rerank', '/v2/rerank',
    '/embeddings', '/v1/embeddings',
    # anthropic
    '/v1/messages',
    # billing
    '/key/info', '/user/info',
]

async def litellm_create_user(user_id, user_alias):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f'{LITELLM_BASEURL}/user/new',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            json={
                'user_id': user_id,
                'user_alias': user_alias,
                'send_invite_email': False,
                'user_role': 'internal_user_viewer',
                'max_budget': GLOBAL_BUDGET,
                'auto_create_key': False,
            }
        )
        resp.raise_for_status()
        return resp.json()
    
async def litellm_create_key(user_id, key_alias):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f'{LITELLM_BASEURL}/key/generate',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            json={
                'key_alias': key_alias,
                'user_id': user_id,
                'send_invite_email': False,
                'max_budget': INSTANCE_BUDGET,
                'max_parallel_requests': 10,
                'allowed_routes': SAFE_ROUTES,
            }
        )
        resp.raise_for_status()
        return resp.json()

async def litellm_delete_key(key):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f'{LITELLM_BASEURL}/key/delete',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            json={
                'keys': [key],
            }
        )
        resp.raise_for_status()
        return resp.json()
    
async def litellm_delete_user(user_id):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f'{LITELLM_BASEURL}/user/delete',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            json={
                'user_ids': [user_id],
            }
        )
        resp.raise_for_status()
        return resp.json()
    
async def litellm_query_user(user_id):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f'{LITELLM_BASEURL}/user/info',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            params={
                'user_id': user_id,
            }
        )
        resp.raise_for_status()
        return resp.json()
    
async def litellm_query_key(key):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f'{LITELLM_BASEURL}/key/info',
            headers={'Authorization': f'Bearer {LITELLM_KEY}'},
            params={
                'key': key,
            }
        )
        resp.raise_for_status()
        return resp.json()

def calc_keyhash(key):
    # https://github.com/BerriAI/litellm/blob/fc1908523061ee31555ab4e307f321c325c12f79/litellm/proxy/_types.py#L207
    assert key.startswith('sk-')
    return hashlib.sha256(key.encode()).hexdigest()[:20]

app = Sanic('litellm-controller')

@app.get('/test_key/create')
async def test_key_create(request):
    await litellm_create_user('test_user', 'test_user')
    res = await litellm_create_key('test_user', 'test_key')
    print(res)
    return text(f'ok\n\nkey: {res["key"]}\n')

@app.get('/test_key/delete')
async def test_key_delete(request):
    await litellm_delete_user('test_user')
    return text('ok\n')

@app.get('/test_key/reset')
async def test_key_reset(request):
    try:
        await litellm_delete_user('test_user')
    except Exception:
        pass

    return await test_key_create(request)

@app.get('/harness/create_user')
async def harness_create_user(request):
    user_id = request.args.get('user_id')
    user_alias = request.args.get('user_alias')
    await litellm_create_user(user_id, user_alias)
    return json({'error': None})

@app.get('/harness/create_key')
async def harness_create_key(request):
    user_id = request.args.get('user_id')
    key_alias = request.args.get('key_alias')
    res = await litellm_create_key(user_id, key_alias)
    return json({'error': None, 'key': res['key'], 'hash': calc_keyhash(res['key'])})

@app.get('/harness/delete_key')
async def harness_delete_key(request):
    key = request.args.get('key')
    await litellm_delete_key(key)
    return json({'error': None})

@app.get('/harness/delete_user')
async def harness_delete_user(request):
    user_id = request.args.get('user_id')
    await litellm_delete_user(user_id)
    return json({'error': None})

@app.get('/harness/query_balance')
async def harness_query_balance(request):
    user_id = request.args.get('user_id')
    key = request.args.get('key')
    
    try:
        user_usage = (await litellm_query_user(user_id))['user_info']['spend']
    except Exception as e:
        print(f'!!! FAILED to query user {user_id}: {type(e)} {e}')
        user_usage = None

    try:
        key_usage = (await litellm_query_key(key))['info']['spend']
    except Exception as e:
        print(f'!!! FAILED to query key {key}: {type(e)} {e}')
        key_usage = None

    return json({'error': None, 'user_usage': user_usage, 'key_usage': key_usage})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True, single_process=True)
