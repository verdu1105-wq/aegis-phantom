"""
AEGIS PHANTOM — Goon Vault Recovery Script
Imports 205 missing accounts back into Redis
Run on Pi: python3.9 recover_goons.py
"""
import redis, os

REDIS_URL = os.getenv("REDIS_URL", "redis://:DTqOikGrN26czTE4b4DWgj5Tkz849aq8@redis-10919.c284.us-east1-2.gce.cloud.redislabs.com:10919")

MISSING_GOONS = [
    "adrian.maldonado688","airbourne_rucca82nd","alejandrogarci5500","alepat199",
    "americansinner702","apple.user72233955","arieshabibi1","armybarbee","batmetal121",
    "borabee22","borncustom","chillinchela1","chriskraft48","christinemommee3",
    "corinaweaver2","datgayrepublicanhoney","drchewy","e079912","edithc2000","ericm7085",
    "erinball","erinhaney205","fuck.tiktok4life","gilbert.calleros3","glitter_farts69",
    "hellogeorge441","howie_feltercooch","i.support5","its_ee4","j.houston246",
    "j.mora7464","jacob_raymond83","jkob4133sgp","justice_seeker_99","kimmypoo3",
    "ladyt.514","lampshade_news","lasagna688","lin.lin.lin78","lordsnow","lostsouloldies",
    "mandarose","marcoherrera7152","marla.hunter5041","militarybratt2022","natedog005_",
    "nursemarsha5219","otg.uncle_andre","pam.price7","retiredandfree247","rosie_posie_18",
    "scumbag8193","shariflattskipton","silversteind","soccerboy0013","sweettooth329",
    "tatertot6996","tikgrannytok2020","tony.mills44","trs1968","tyguyin3",
    "unclerustytheclown","user021494711","user023378902","user070356803","user09515506",
    "user1038211847224","user1118076426749","user113113695407","user1151950104612",
    "user1157273418183","user1170504805","user12618171628371","user1319581607498",
    "user1385959226029","user160037187","user16369023611847","user1678429674367",
    "user1696345785764","user1738110677957","user1751265266133","user1798010137579",
    "user1938443901792","user214325451","user2150214794074","user2258378958809",
    "user2332948195845","user23390532617161","user2340088272839","user2391612061625",
    "user2437010553940","user2619717956582","user2664069089994","user27877800679358",
    "user2795525114231","user299237054331","user29952511556193","user31048713729",
    "user310558704","user310961985047","user31263808523767","user3126876320810",
    "user3168512422017","user31999661685082","user3238129477737","user3254545040082",
    "user3305579261442","user3470731576146","user34830950660777","user3519953535750",
    "user3552050802233","user3793917594558","user382145159","user3866312795985",
    "user3935854865736","user396797364","user4040323951294","user4170456904621",
    "user4322952276299","user453226175997","user4595960887141","user4596566376143",
    "user4699627756686","user4736124157144","user4822409480158","user4861542231810",
    "user5073162872349","user508578057999","user510703023","user51467125625",
    "user522079124","user5245856288710","user54156581748697","user5431401522701",
    "user5434879670879","user546186887","user5511317162047","user5512240683763",
    "user5563554558182","user55651862109245","user5731117174697","user5748586672331",
    "user583484069","user5879423847017","user590453081","user6018752716267",
    "user6039932381813","user61990609937631","user6218373416572","user6358997655",
    "user648743769","user6541402088870","user654820243","user6740144310936",
    "user6770049560022","user6856183995997","user6867302962995","user6954810712105",
    "user695603566","user6979715368","user71438688353582","user7379504044315",
    "user739471471","user7541410171819","user7554351304667","user756664963",
    "user7575380607266","user7624777409584","user7726800741228","user7811798432942",
    "user7885283442341","user795956845239","user7962928188143","user79769550614",
    "user8002146281903","user81378497279847","user814165442774","user81868132617835",
    "user8187633414968","user8204352032992","user8332185253073","user8459020934972",
    "user8613688191444","user86681122456","user880212330","user899388908",
    "user9044769542483","user9170947842915","user9272473607320","user92733097475053",
    "user9327778770152","user9528652148708","user95710159","user96052754",
    "user9617670373","user96910529401583","user9697057550597","user970429856",
    "user97047090929034","user9720690884473","user9872565270661","user9939115319678",
    "user994056778106","user9956182325454","whosegoodlooking"
]

# Whitelist additions
WHITELIST_ADDITIONS = [
    "letmebetheone","munfordal","jet1939","spicycyber","selenaperez26",
    "christophermicken3","damn_geenah","jr189197","welshfarmertravels",
    "thedisneyprogressive","d4rkn8t","infernalfreakshow","tksdaddy1",
    "jimmywilliams3041","shorty8251","bestman8811","puppyluv2020",
    "activist_derek_","kenneth.cupps4","wakandan_sentinel03","diavatalks"
]

r = redis.from_url(REDIS_URL, decode_responses=True)

# First normalize existing entries — convert JSON to plain strings
import json
converted = 0
for m in list(r.smembers("goons")):
    try:
        d = json.loads(m)
        username = d.get("username","").lower().strip()
        if username and 'get-content' not in username:
            r.srem("goons", m)
            r.sadd("goons", username)
            converted += 1
        elif not username or 'get-content' in username:
            r.srem("goons", m)
    except:
        pass

print(f"Normalized {converted} existing entries")

# Add missing goons
added = 0
for u in MISSING_GOONS:
    u = u.lower().strip()
    if u:
        r.sadd("goons", u)
        added += 1

print(f"Added {added} missing goons")

# Add whitelist
wl_added = 0
for u in WHITELIST_ADDITIONS:
    u = u.lower().strip()
    if u:
        r.sadd("whitelist", u)
        wl_added += 1

print(f"Added {wl_added} whitelist entries")
print(f"\nFinal vault count: {r.scard('goons')}")
print(f"Final whitelist count: {r.scard('whitelist')}")
