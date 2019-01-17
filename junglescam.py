import sys
import urllib3
import certifi
import re
import os
import random
import time
from json import loads
import socket

from urllib3.contrib.socks import SOCKSProxyManager
from bs4 import BeautifulSoup
from tqdm import tqdm
import asyncio
import aiohttp
import sqlite3

# setup colored output
from colorama import init
init(autoreset=True)
from colorama import Fore, Back, Style

print (Fore.YELLOW + """
888888                            888           .d8888b.
    "88b                            888          d88P  Y88b
     888                            888          Y88b.
     888 888  888 88888b.   .d88b.  888  .d88b.   "Y888b.    .d8888b  8888b.  88888b.d88b.
     888 888  888 888 "88b d88P"88b 888 d8P  Y8b     "Y88b. d88P"        "88b 888 "888 "88b
     888 888  888 888  888 888  888 888 88888888       "888 888      .d888888 888  888  888
     88P Y88b 888 888  888 Y88b 888 888 Y8b.     Y88b  d88P Y88b.    888  888 888  888  888
     888  "Y88888 888  888  "Y88888 888  "Y8888   "Y8888P"   "Y8888P "Y888888 888  888  888
   .d88P                        888
 .d88P"                    Y8b d88P
888P"                       "Y88P"
""")
print(Fore.CYAN + 'An Amazon OSINT scraper for potential scam accounts')
print(Fore.YELLOW + 'By @jakecreps & @noneprivacy')
print(Fore.CYAN + 'Insert your keyword')
baseUrl = 'https://www.amazon.com/s/ref=nb_sb_noss?url=search-alias%3Daps&field-keywords=' + input()
print(Fore.CYAN + 'Which pages do you want to scan? (eg: 1-5)')
pages = input().split('-')
print(Fore.CYAN + 'Maximum Seller Feedback (%)')
threshold = input()
print(Fore.CYAN + 'What do you want to call the database? (if it does not exist, a new one will be created)')
dbName = input() + ".db"
print(Fore.CYAN + 'Use Tor to round-robin requests? (Y/N)')
torSupport = input()
if torSupport.lower() == "y":
    torSupport = True
else:
    torSupport = False

_products_id = {}
_sellers_id = {}
rmScores = {
    '3': 'Fail',
    '2': 'Warn',
    '1': 'Pass',
    '0': 'Zero'
}

roundRobin = 0
torPort = '9050' # 9150 if using Tor Browser
torControlPort = 9051 # 9151 if using Tor Browser
torControlPW = 'password' # append `tor --hash-password "password"` to torrc
# HashedControlPassword 16:86B89B9EDE48F177605F9BA0732B4BB67B0AC2004F197FBA13A91C95C1
# don't do this if you don't know what you are doing

def initDB(db):
    dbConnector = sqlite3.connect(db)
    cursor = dbConnector.cursor()

    tableProducts = """
        CREATE TABLE IF NOT EXISTS
            products (
                id TEXT PRIMARY KEY NOT NULL,
                rm_score TEXT
            );
        """
    cursor.execute(tableProducts)

    tableSellers = """
        CREATE TABLE IF NOT EXISTS
            sellers (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                JL INTEGER,
                feedback INTERGER
            );
        """
    cursor.execute(tableSellers)

    tableDesc = """
        CREATE TABLE IF NOT EXISTS
            extras (
                id TEXT NOT NULL,
                contact INTEGER,
                gmail INTEGER,
                yahoo INTEGER,
                paypal INTEGER,
                FOREIGN KEY(id) REFERENCES sellers(id)
            );
        """
    cursor.execute(tableDesc)

    tableWhoSellsWhat = """
        CREATE TABLE IF NOT EXISTS
            wsw (
                product_id TEXT NOT NULL,
                seller_id TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id),
                FOREIGN KEY(seller_id) REFERENCES sellers(id)
            );
        """
    cursor.execute(tableWhoSellsWhat)

    return dbConnector

dbConnector = initDB(dbName)

def insertProduct(productID, rmScore):
    try:
        cursor = dbConnector.cursor()
        cursor.execute('INSERT INTO products VALUES(?,?)', (productID, rmScore))
        dbConnector.commit()
    except sqlite3.IntegrityError:
        pass

def insertSeller(productID, sellerInfo):
    try:
        cursor = dbConnector.cursor()
        cursor.execute('INSERT INTO wsw VALUES(?,?)', (productID, sellerInfo[0]))
        dbConnector.commit()
    except sqlite3.IntegrityError:
        pass
    try:
        cursor.execute('INSERT INTO sellers VALUES(?,?,?,?)', sellerInfo)
        dbConnector.commit()
    except sqlite3.IntegrityError:
        pass

def insertExtra(sellerID, extras):
    _contact = ('contact' in extras)*1
    _gmail = ('gmail' in extras)*1
    _yahoo = ('yahoo' in extras)*1
    _paypal = ('paypal' in extras)*1
    _extras = (sellerID, _contact, _gmail, _yahoo, _paypal)
    try:
        cursor = dbConnector.cursor()
        cursor.execute('INSERT INTO extras VALUES(?,?,?,?,?)', _extras)
        dbConnector.commit()
    except sqlite3.IntegrityError:
        pass

def getInsertedSellers():
    cursor = dbConnector.cursor()
    cursor.execute('SELECT * FROM wsw')
    allRows = cursor.fetchall()
    with tqdm(total=len(allRows), desc='[<] Retrieving stored sellers') as cursorBar:
        for row in allRows:
            _sellers_id[row[1]] = {row[0] : True}
            cursorBar.update(1)
    cursorBar.close()

def newTorIdentity():
    tor_c = socket.create_connection(('127.0.0.1', torControlPort))
    tor_c.send('AUTHENTICATE "{}"\r\nSIGNAL NEWNYM\r\n'.format(torControlPW).encode())
    response = tor_c.recv(1024)
    if response == b'250 OK\r\n250 OK\r\n':
        print('[+] new Tor identity')

def getRandomUA():
    _httpPool = urllib3.PoolManager( 1,
        cert_reqs='CERT_REQUIRED',
        ca_certs=certifi.where())
    url = "https://fake-useragent.herokuapp.com/browsers/0.1.8"
    r = _httpPool.request('GET', url).data.decode('utf-8')
    browsers = loads(r)['browsers']
    return browsers

browsers = getRandomUA()

def randomUserAgent():
    return random.choice(browsers[random.choice(list(browsers))])

def pageRequest(url):
    global roundRobin
    proxy = SOCKSProxyManager('socks5://localhost:'+str(torPort),
        cert_reqs='CERT_REQUIRED',
        ca_certs=certifi.where(),
        headers={'user-agent': randomUserAgent(), 'Cookie': ''})
    http = urllib3.PoolManager( 1,
        cert_reqs='CERT_REQUIRED',
        ca_certs=certifi.where(),
        headers={'user-agent': randomUserAgent(), 'Cookie': ''})
    if roundRobin % 2:
        response = http.request('GET', url)
    else:
        if torSupport:
            response = proxy.request('GET', url)
        else:
            response = http.request('GET', url)
    roundRobin += 1
    if not roundRobin % 60:
        newTorIdentity()
    return response.data

def reviewMetaScore(itemID):
    url = f'https://reviewmeta.com/api/amazon/{itemID}'
    response = pageRequest(url)
    return response

async def asyncRequest(url):
    timeout = aiohttp.ClientTimeout(total=60*3)
    ua = {'user-agent': randomUserAgent(), 'Cookie': ''}
    async with aiohttp.ClientSession(headers=ua) as session:
        try:
            async with await session.get(url, timeout=timeout) as response:
                return await response.read()
        except aiohttp.client_exceptions.ClientConnectorError:
            print(Fore.RED + "\n[x] Error while fetching data from Amazon!")

def productIdsExtractor(soup):
    global _products_id
    for link in soup.find_all('a', href=re.compile('/dp/[\w]{2,20}/ref=sr_1_[\d]{1,3}')):
        l = link.get('href')
        _l = l.split('/')
        try:
            a = _products_id[_l[5]]
        except KeyError:
            _products_id.update({_l[5]: l})
    return _products_id

def sellerListExtractor(sellerListLink, sbar):
    divs = []
    while True:
        _htmlContent = pageRequest(sellerListLink)
        _soup = BeautifulSoup(_htmlContent, 'lxml')
        if _soup:
            try:
                _t = _soup.find('title').text
                if _t == 'Sorry! Something went wrong!':
                    newTorIdentity()
                    sbar.write(sellerListLink)
                    sbar.write('[x] {}'.format(_t))
                    sbar.write('[*] waiting 10 sec...')
                    time.sleep(10)
                else:
                    _divs = _soup.find_all('div', attrs = {'class': 'a-row a-spacing-mini olpOffer'})
                    for _d in _divs:
                        divs.append(_d)
                    sellerListLink = _soup.find('li', attrs = {'class': 'a-last'})
                    try:
                        a = sellerListLink.find('a')['href']
                    except Exception as e:
                        break
                    sellerListLink = site + sellerListLink.find('a')['href']
            except AttributeError:
                sbar.write("[x] can't find title, going to wait and retry")
                sbar.write('[*] waiting 10 sec...')
                time.sleep(10)
    return divs


def sellerIdExtractor(link, sbar):
    try:
        _seller_id = link.split("seller=")[1]
        return _seller_id
    except:
        sbar.write('[x] got a redirection to another website')
        return False

def sellerFeedbackExtractor(soup):
    _out_of = soup.find_all('span', attrs = {'class': 'a-color-success'})
    if _out_of:
        try:
            _feedback = list(_out_of)[len(_out_of) - 1].text
            return _feedback
        except:
            print(Fore.RED + "\n[x] Error while getting feedback from seller" +
                 ", please check manually the next result")
    return '-1'

def sellerDescExtractor(soup):
    about = soup.find('span', id='about-seller-text')
    if about:
        _text = about.text
        _whatToFind = ['contact', 'gmail', 'yahoo', 'paypal']
        _about = ""
        for w in _whatToFind:
            if w in _text:
                _about += w + ','
        _about[:len(_about)-1]
        return _about
    return ''

def sellerJustLaunched(soup):
    JL_bool = soup.find('span', id='feedback-no-rating')
    if JL_bool:
        return 'True'
    return ''

async def extractSellerInfo(link, itemID, sbar):
    sellerID = sellerIdExtractor(link, sbar)
    if sellerID:
        try:
            _sID = _sellers_id[sellerID][itemID]
            return {}
        except KeyError:
            _sellers_id[sellerID] = {itemID: True}
            url = site + link
            _htmlContent = pageRequest(url)
            _soup = BeautifulSoup(_htmlContent, 'lxml')
            JL_bool = sellerJustLaunched(_soup)
            sellerFull = {
                'id': sellerID,
                'feedback': '',
                'desc': '',
                'just-launched': JL_bool
            }
            if not JL_bool:
                sellerFull['feedback'] = sellerFeedbackExtractor(_soup)
                if int(sellerFull['feedback']) > int(threshold):
                    return {}
            sellerFull['desc'] = sellerDescExtractor(_soup)
            return sellerFull
    return {}

async def fetchSellersFull(itemID, sbar):
    checkUrl = f"https://www.amazon.com/gp/offer-listing/{itemID}/ref=dp_olp_new_center?ie=UTF8"
    rmScore = loads(reviewMetaScore(itemID))['s_overall']
    while not rmScore:
        sbar.write(Fore.YELLOW + '[x] item not scanned yet.\n' +
                   'Please open the next link in the browser, scan the product and press enter.')
        sbar.write(f'https://reviewmeta.com/amazon/{itemID}')
        sbar.write(Fore.YELLOW + '[!] if there aren\'t any reviews for this product, just type \"0\"')
        _input = input('\n[>]')
        if _input:
            rmScore = '0'
        else:
            rmScore = loads(reviewMetaScore(itemID))['s_overall']
    _rmScore = rmScores[rmScore]
    insertProduct(itemID, _rmScore)
    divs = sellerListExtractor(checkUrl, sbar)
    for div in divs:
        _name = div.find('h3', attrs = {'class': 'olpSellerName'})
        name = _name.text.strip()
        if name:
            sellerLink = _name.find('a')['href']
            sellerFull = await extractSellerInfo(sellerLink, itemID, sbar)
            if sellerFull:
                if not sellerFull['feedback'] == '-1':
                    sbar.write("<-> " + name + "\n |-> id: " + sellerFull['id']
                        + "\n |-> just-launched: " + sellerFull['just-launched']
                        + "\n |-> feedback: " + sellerFull['feedback']
                        + "\n |-> desc: " + sellerFull['desc']
                        + "\n --> Review Meta Score: " + _rmScore)
                    _t_JL = 0
                    if sellerFull['just-launched']:
                        _t_JL = 1
                    try:
                        _t_feedback = int(sellerFull['feedback'])
                    except ValueError:
                        _t_feedback = -2
                    _sellerFull = (sellerFull['id'], str(name), _t_JL, _t_feedback)
                    insertSeller(itemID, _sellerFull)
                    insertExtra(sellerFull['id'], sellerFull['desc'])
        sbar.update(1)

site = "https://" + baseUrl.split('/')[2]

tasks = []
loop = asyncio.get_event_loop()

fPage = int(pages[0])
lPage = int(pages[1])

_tqdm_desc = "[<] Extracting ids from pages"
with tqdm(total=lPage, desc=_tqdm_desc) as pbar:
    getInsertedSellers()
    loop = asyncio.get_event_loop()
    for i in range(lPage):
        htmlContent = pageRequest(baseUrl)
        soup = BeautifulSoup(htmlContent, 'lxml')
        if soup.find('title').text == 'Robot Check':
            pbar.write('[x] Captcha found, wait a while before retrying or change the IP!')
        else:
            nextPage = soup.find('a', attrs = {'id': 'pagnNextLink'})['href']
            baseUrl = site + nextPage
            if i >= fPage:
                IDs = productIdsExtractor(soup)
                if not len(IDs):
                    pbar.write("[x] Amazon is blocking your requests, please change IP")
                    exit()
                for key in IDs:
                    task = asyncio.ensure_future(fetchSellersFull(key, pbar))
                    tasks.append(task)
            pbar.update(1)
    pbar.clear()
    pbar.set_description("[<] Extracting sellers info")
    loop.run_until_complete(asyncio.wait(tasks))
    loop.close()
    dbConnector.close()
