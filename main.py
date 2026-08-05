import requests
from bs4 import BeautifulSoup
import json

USER_AGENT_CONTACT = "katarz.szczepaniak@gmail.com"
HEADER = {"User-Agent": f"PortfolioProjectResearch/0.1 (kontakt: {USER_AGENT_CONTACT})"}

# get page html code 
response = requests.get("https://pracuj.pl/praca", headers=HEADER)
json_data = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__").string.encode()
json_data = json.loads(json_data)

data = json_data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["groupedOffers"]

single_url = data[0]['offers'][0]['offerAbsoluteUri']

# save json 
with open("text.json", "w", encoding="utf-8") as f:
    json.dump(data, f,ensure_ascii=False, indent=4)

# get offer details
details = requests.get(single_url, headers=HEADER)

json_details = BeautifulSoup(details.text, "html.parser").find("script", id="__NEXT_DATA__").string
json_details = json.loads(json_details)

json_details = json_details["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]

with open("details.json", "w", encoding="utf-8") as f:
    json.dump(json_details, f,ensure_ascii=False, indent=4)