import requests
from bs4 import BeautifulSoup
import json
from time import sleep
import os
import pathlib

USER_AGENT_CONTACT = "katarz.szczepaniak@gmail.com"
HEADER = {"User-Agent": f"PortfolioProjectResearch/0.1 (kontakt: {USER_AGENT_CONTACT})"}
BASE_URL = "https://pracuj.pl/praca"
TEST_DATA_DIR = pathlib.Path("test_data").mkdir(parents=True, exist_ok=True)


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
        print(f"Loaded {page_number}. page")
        page_number += 1
        sleep(2)
        response = get_search_output_json(f"{base_url}?pn={page_number}", header)
    return whole_output
        


def get_details_urls(offers_json: json)-> list:
    urls_list = []
    for element in offers_json:
        urls_list.append(element["offers"][0]["offerAbsoluteUri"])
    return urls_list


def get_details(url: str, header: dict)-> json:
    details = requests.get(url, headers=header)

    json_details = BeautifulSoup(details.text, "html.parser").find("script", id="__NEXT_DATA__").string
    json_details = json.loads(json_details)

    json_details = json_details["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]
    return json_details


def extract_details_from_json(offer_json: json):
    '''
    - offer id
    - employerId
    - jobOfferLanguage
    - dateOfInitialPublicationUtc
    - lastPublishedUtc
    - expirationDateUtc
    - isActive
    - jobTitle
    - isArchive
    - isWithdrawn
    - offerAbsoluteUrl
    - displayEmployerName
    - cateogries
    - leadingCategory
    - workplaces
    - positionLevels
    - entirelyRemoteWork
    - workSchedules
    - typesOfContracts with salary inside)
    - salary 
    - workModes
    - "sectionType": "requirements"
    - "sectionType": "offered"
    - "code": "many-vacancies"
    - "code": "immediate-employment"
    - "code": "shift-work"
    '''
    pass

def get_all_details(url_list, header, limit:int = None):
    all_details = []
    for url, idx in zip(url_list, range(len(url_list))):
        if limit is not None and idx >= limit:
            break

        sleep(2)
        json_details = get_details(url, header)
        all_details.append(json_details)
    print(f"Loaded {len(all_details)} offer details")
    return all_details

def save_to_db():
    pass



ulr_list = get_all_search_output(BASE_URL, HEADER, limit_pages=1)

with open("test_data/text.txt", "w", encoding="utf-8") as f:
    f.write('\n'.join(ulr_list) + '\n')

details = get_all_details(ulr_list, header=HEADER, limit=3)

for element, idx in zip(details, range(len(details))):
    with open(f"test_data/details{idx}.json", "w", encoding="utf-8") as f:
        json.dump(details,f, indent=4, ensure_ascii=False)


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
