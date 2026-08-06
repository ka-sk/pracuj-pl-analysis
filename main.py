import requests
from bs4 import BeautifulSoup
import json
from time import sleep

USER_AGENT_CONTACT = "katarz.szczepaniak@gmail.com"
HEADER = {"User-Agent": f"PortfolioProjectResearch/0.1 (kontakt: {USER_AGENT_CONTACT})"}
BASE_URL = "https://pracuj.pl/praca"


def get_search_output_json(url: str, header: dict) -> json: 
    # get page html code 
    try:
        response = requests.get(url, headers=header)
        json_data = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__").string
        json_data = json.loads(json_data)
        # initial filtering out unnecessary tags
        json_data = json_data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["groupedOffers"]

    except Exception as e:
        raise e
    
    return json_data


def get_all_search_output(base_url: str, header: dict, limit_pages:int = None):
    page_number = 1
    whole_output = []
    response = get_search_output_json(base_url, header)

    while response != [] and page_number != limit_pages+1:
        response = get_details_urls(response)
        whole_output += response
        page_number += 1
        sleep(2)
        response = get_search_output_json(f"{base_url}?pn={page_number}", header)
        print(f"Loaded {page_number}. page")
    return whole_output
        


def get_details_urls(offers_json: json)-> list:
    urls_list = []
    for element in offers_json:
        urls_list.append(element["offers"][0]["offerAbsoluteUri"])
    return urls_list

with open("text.json", "w", encoding="utf-8") as f:
    f.write('\n'.join(get_all_search_output(BASE_URL, HEADER, limit_pages=3)) + '\n')

"""
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
    json.dump(json_details, f,ensure_ascii=False, indent=4)"""
