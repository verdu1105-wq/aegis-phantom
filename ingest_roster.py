# AEGIS — EE Network Full Roster Ingest
# Source: Jess block list upload, 2026-05-05
# Case: JESS-2026-05-04-CRITICAL

EE_ROSTER = {
    "high_priority": [
        {"username": "heven_scent4",         "role": "respondent_backup",    "tier": "named", "network": "EE"},
        {"username": "respectfullyno123183",  "role": "ee_2.0_sleeper",       "tier": "primary","network": "EE"},
        {"username": "liberal_hater6969",     "role": "stolen_valor_agitator","tier": "named", "network": "EE"},
        {"username": "nobodies_2cents",       "role": "dehumanization",       "tier": "named", "network": "EE"},
        {"username": "ticktockref4",          "role": "intimidation_node",    "tier": "named", "network": "EE"},
        {"username": "snake.eyes.76",         "role": "hostile_agitator",     "tier": "named", "network": "EE"},
        {"username": "macycreamer22",         "role": "bridge_scout",         "tier": "named", "network": "EE"},
        {"username": "dinehebrew",            "role": "active_agitator",      "tier": "named", "network": "EE"},
        {"username": "sugar_daddy_067",       "role": "intimidation_node",    "tier": "named", "network": "EE"},
    ],
    "vampire_burners": [
        {"username": u, "role": "api_saturation", "tier": "burner", "network": "EE",
         "ttp": "numeric_string_DoS"}
        for u in [
            "user7841180269021","user8532074734702","user9825932381635",
            "user4992030526386","user2138115069121","user460238482",
            "user2405816978043","user3651238570985","user20321214116248",
            "user1903439732839","user5584543008540","user2650463561045",
            "user170815293974","user82622203667793","user70368333442952",
            "user2440111717774","user6676592995960","user3989960919128",
        ]
    ],
    "camouflage": [
        {"username": u, "role": "stealth_infiltrator", "tier": "camouflage",
         "network": "EE", "ttp": "benign_handle_spoofing"}
        for u in [
            "chickensarefriends","cutebunny3872","back2black",
            "sunnie2297","seaview429","emilialamy",
            "miss_british11","good2go","deegan.jane",
        ]
    ],
}

async def ingest_ee_roster(roster: dict, case_id: str = "JESS-2026-05-04-CRITICAL"):
    pipe = redis_client.pipeline()
    total = 0
    for category, accounts in roster.items():
        for acct in accounts:
            acct["case"] = case_id
            acct["ingested"] = int(time.time())
            acct["source"] = "jess_blocklist_20260505"
            key = f"vault:{acct['username']}"
            pipe.hset(key, mapping=acct)
            pipe.sadd("vault:index", acct["username"])
            pipe.sadd(f"vault:network:EE", acct["username"])
            pipe.sadd(f"vault:category:{category}", acct["username"])
            pipe.sadd(f"vault:tier:{acct['tier']}", acct["username"])
            total += 1
    await pipe.execute()
    print(f"[VAULT] Ingested {total} accounts — case {case_id}")
    return total