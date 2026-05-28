"""Add new accounts to AEGIS PHANTOM vault"""
import redis, os
from dotenv import load_dotenv

load_dotenv(r'C:\Users\VernonDunbar\Documents\Aegis_Phantom\.env')
r = redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)

accounts = [
    {"username":"camlikecam",       "status":"known_hostile","risk_score":"0.85","notes":"backup evasion — explicit burner"},
    {"username":"consuelocardenas82","status":"known_hostile","risk_score":"0.80","notes":"ghost burner — 0 likes 0 videos 712 following"},
    {"username":"florinemooring",   "status":"suspected",    "risk_score":"0.60","notes":"raid participant"},
    {"username":"willeejones",      "status":"suspected",    "risk_score":"0.65","notes":"private lurker — 661 following"},
    {"username":"lupemags",         "status":"suspected",    "risk_score":"0.60","notes":"zero engagement burner"},
    {"username":"redbuddah69",      "status":"suspected",    "risk_score":"0.60","notes":"private lurker — 733 following"},
    {"username":"keithshelton709",  "status":"suspected",    "risk_score":"0.75","notes":"follow-network account — 9986 following"},
]

pipe = r.pipeline()
for a in accounts:
    key = f"vault:u:{a['username']}"
    pipe.hset(key, mapping=a)
pipe.execute()
print(f"Added {len(accounts)} accounts to vault")
