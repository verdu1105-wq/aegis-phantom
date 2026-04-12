import redis, json

r = redis.Redis(
    host='redis-10919.c284.us-east1-2.gce.cloud.redislabs.com',
    port=10919,
    password='LrQmHLtx7RjHAeGis26',
    ssl=False,
    decode_responses=True
)

with open('goon_export.json') as f:
    data = json.load(f)

goons = data['goons']
count = 0
for username in goons:
    if username:
        r.sadd('goons', username)
        count += 1

print(f'Loaded {count} goons into vault')
print(f'Vault size: {r.scard("goons")}')