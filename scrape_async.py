import asyncio
import datetime
import json
import logging
import os
import re
import sys

import aiohttp
import boto3

# configure logging
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger("botocore").setLevel(logging.CRITICAL)
logging.getLogger("boto3").setLevel(logging.CRITICAL)
LOG = logging.getLogger("inStock")
# JSON Config for products and SNS topics
CONFIG_FILE = os.path.join(os.path.abspath(
    os.path.dirname(__file__)), 'config.json')
# Postman works great, but urllib and even my browser's string were
# hanging... Whatever as long as we get HTML back!
USER_AGENT = "PostmanRuntime/7.26.10"
# Timeout (in seconds) for individual product page downloads
TIMEOUT = 30
# Regex for parsing the html
RE_BUTTON_STATE = re.compile(r'"buttonState":"(.*?)"')
# The table we use to track if notifications have been sent
IN_STOCK_TABLE = "inStock"
# Number of seconds we should stop sending notifications after a product
# comes in stock
IN_STOCK_EXP = 300


def notify(product):
    LOG.debug("notify(): %s", product["title"])
    try:
        sns = boto3.resource('sns')
        ddb = boto3.resource('dynamodb')
        table = ddb.Table(IN_STOCK_TABLE)
        for arn in product["snsTopicArns"]:
            item = table.get_item(
                Key={"url": product["url"], "arn": arn}).get('Item')
            if item is None:
                topic = sns.Topic(arn)
                topic.publish(
                    Subject="Your product is in stock at BestBuy!".format(product["title"]),
                    Message="\n\n{} is in stock at BestBuy!\n\n{}".format(
                        product["title"],
                        product["url"],
                    ),
                )
                LOG.info("notification sent: %s, %s", arn, product["url"])
                table.put_item(Item={
                    "url": product["url"],
                    "arn": arn,
                    "inStock": int(datetime.datetime.now().timestamp()) + IN_STOCK_EXP,
                })
                LOG.debug("notifications paused for %s seconds: %s", IN_STOCK_EXP, arn)
            else:
                diff = item["inStock"] - int(datetime.datetime.now().timestamp())
                LOG.debug("notifications paused for %s seconds: %s", diff, arn)
    except Exception:
        LOG.exception("error sending notification: %s :: %s", arn, product["url"])


async def get_product_page(session, product):
    LOG.debug("get_product_page(): %s", product["title"])
    try:
        async with session.get(
            product["url"],
            headers={
                "Accept": "*/*",
                "Cache-Control": "no-cache",
                "Host": 'www.bestbuy.com',
                "User-Agent": USER_AGENT,
            },
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as response:
            LOG.debug("downloaded: %s", product["url"])
            html = await response.text()
            match = RE_BUTTON_STATE.search(html)
            if match:
                button_state = match.group(1)
                if button_state in ["ADD_TO_CART", "CHECK_STORES"]:
                    LOG.info("product available (%s): %s", button_state, product["title"])
                    notify(product)
                else:
                    LOG.info("product unavailable (%s): %s", button_state, product["title"])
            else:
                LOG.debug("button state not found: %s", product["title"])
    except asyncio.exceptions.TimeoutError:
        LOG.warning('request timed out: %s', product["url"])


async def get_all_product_pages(products):
    LOG.debug("get_all_product_pages()")
    async with aiohttp.ClientSession() as session:
        tasks = []
        for product in products:
            task = asyncio.ensure_future(get_product_page(session, product))
            tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    try:
        with open(CONFIG_FILE) as fin:
            config = json.load(fin)
    except Exception as e:
        print(e)
        sys.exit(1)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    asyncio.get_event_loop().run_until_complete(
        get_all_product_pages(config["products"]))
