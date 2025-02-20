from time import sleep

import requests
from ibflex import client, parser, FlexQueryResponse, BuySell
from datetime import datetime
import json


def get_cash_amount_from_flex(query):
    cash = 0
    try:
        cash += query.FlexStatements[0].CashReport[0].endingCash
    except Exception as e:
        print(e)
    try:
        cash += query.FlexStatements[0].CashReport[0].endingCashPaxos or 0
    except Exception as e:
        print(e)
    return cash


def generate_chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def format_act(act):
    symbol_nested = act.get("SymbolProfile", {"symbol": ""}).get("symbol")
    return {
        "accountId": act["accountId"],
        "date": act["date"][0:18],
        "fee": float(act["fee"]),
        "quantity": act["quantity"],
        "symbol": act.get("symbol", symbol_nested),
        "type": act["type"],
        "unitPrice": act["unitPrice"],
    }


def is_act_present(act_search, acts):
    for act in acts:
        act1 = format_act(act)
        act2 = format_act(act_search)
        if act1 == act2:
            return True
    return False


def get_diff(old_acts, new_acts):
    diff = []
    for new_act in new_acts:
        if not is_act_present(new_act, old_acts):
            diff.append(new_act)
    return diff


class SyncIBKR:
    IBKRCATEGORY = "0494d003-b0d3-468a-9674-ad642d6b7a57"

    def __init__(self, ghost_host, ibkrtoken, ibkrquery, ghost_token, ghost_currency):
        self.ghost_token = ghost_token
        self.ghost_host = ghost_host
        self.ghost_currency = ghost_currency
        self.ibkrtoken = ibkrtoken
        self.ibkrquery = ibkrquery

    def sync_ibkr(self):
        print("Fetching Query")
        response = client.download(self.ibkrtoken, self.ibkrquery)
        print("Parsing Query")
        query: FlexQueryResponse = parser.parse(response)
        activities = []
        date_format = "%Y-%m-%d"
        account_id = self.create_or_get_IBKR_accountId()
        if account_id == "":
            print("Failed to retrieve account ID closing now")
            return
        self.set_cash_to_account(account_id, get_cash_amount_from_flex(query))
        for trade in query.FlexStatements[0].Trades:
            if trade.openCloseIndicator is None:
                print("trade is not open or close (ignoring): %s", trade)
            elif trade.openCloseIndicator.CLOSE:
                date = datetime.strptime(str(trade.tradeDate), date_format)
                iso_format = date.isoformat()
                symbol = trade.symbol
                if ".USD-PAXOS" in trade.symbol:
                    symbol = trade.symbol.replace(".USD-PAXOS", "") + "USD"
                elif "VUAA" in trade.symbol:
                    symbol = trade.symbol + ".L"
                elif "VWRP" in trade.symbol:
                    symbol = trade.symbol + ".L"
                elif "EUR" in trade.symbol:
                    symbol = trade.symbol.replace(".", "") + "=X"
                if trade.buySell == BuySell.BUY:
                    buysell = "BUY"
                elif trade.buySell == BuySell.SELL:
                    buysell = "SELL"
                else:
                    print("trade is not buy or sell (ignoring): %s", trade)
                    continue

                activities.append(
                    {
                        "accountId": account_id,
                        "comment": None,
                        "currency": trade.currency,
                        "dataSource": "YAHOO",
                        "date": iso_format,
                        "fee": float(0),
                        "quantity": abs(float(trade.quantity)),
                        "symbol": symbol.replace(" ", "-"),
                        "type": buysell,
                        "unitPrice": float(trade.tradePrice),
                    }
                )

        diff = get_diff(self.get_all_acts_for_account(account_id), activities)
        if len(diff) == 0:
            print("Nothing new to sync")
        else:
            self.import_act(diff)

    def set_cash_to_account(self, account_id, cash):
        if cash == 0:
            print("No cash set, no cash retrieved")
            return False
        account = {
            "accountType": "SECURITIES",
            "balance": float(cash),
            "id": account_id,
            "currency": self.ghost_currency,
            "isExcluded": False,
            "name": "IBKR",
            "platformId": self.IBKRCATEGORY,
        }

        url = f"{self.ghost_host}/api/v1/account/{account_id}"

        payload = json.dumps(account)
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request("PUT", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return False
        if response.status_code == 200:
            print(f"Updated Cash for account {response.json()['id']}")
        else:
            print("Failed create: " + response.text)
        return response.status_code == 200

    def delete_act(self, act_id):
        url = f"{self.ghost_host}/api/v1/order/{act_id}"

        payload = {}
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
        }
        try:
            response = requests.request("DELETE", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return False

        return response.status_code == 200

    def import_act(self, bulk):
        chunks = generate_chunks(bulk, 10)
        for acts in chunks:
            url = f"{self.ghost_host}/api/v1/import"
            formatted_acts = json.dumps(
                {"activities": sorted(acts, key=lambda x: x["date"])}
            )
            payload = formatted_acts
            headers = {
                "Authorization": f"Bearer {self.ghost_token}",
                "Content-Type": "application/json",
            }
            print("Adding activities: " + formatted_acts)
            try:
                response = requests.request("POST", url, headers=headers, data=payload)
            except Exception as e:
                print(e)
                return False
            if response.status_code == 201:
                print(f"created {formatted_acts}")
            else:
                print("Failed create: " + response.text)
            if response.status_code != 201:
                return False
        return True

    def addAct(self, act):
        url = f"{self.ghost_host}/api/v1/order"

        payload = json.dumps(act)
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
            "Content-Type": "application/json",
        }
        print("Adding activity: " + json.dumps(act))
        try:
            response = requests.request("POST", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return False
        if response.status_code == 201:
            print(f"created {response.json()['id']}")
        else:
            print("Failed create: " + response.text)
        return response.status_code == 201

    def create_ibkr_account(self):
        account = {
            "accountType": "SECURITIES",
            "balance": 0,
            "currency": self.ghost_currency,
            "isExcluded": False,
            "name": "IBKR",
            "platformId": "d6a6e5b3-f1d8-421d-b7c3-139e5acfa857",
        }

        url = f"{self.ghost_host}/api/v1/account"

        payload = json.dumps(account)
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request("POST", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return ""
        if response.status_code == 201:
            return response.json()["id"]
        print("Failed creating ")
        return ""

    def get_account(self):
        url = f"{self.ghost_host}/api/v1/account"

        payload = {}
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
        }
        try:
            response = requests.request("GET", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return []
        if response.status_code == 200:
            return response.json()["accounts"]
        else:
            raise Exception(response)

    def create_or_get_IBKR_accountId(self):
        accounts = self.get_account()
        for account in accounts:
            if account["name"] == "IBKR":
                return account["id"]
        return self.create_ibkr_account()

    def delete_all_acts(self):
        account_id = self.create_or_get_IBKR_accountId()
        acts = self.get_all_acts_for_account(account_id)

        if not acts:
            print("No activities to delete")
            return True
        complete = True

        for act in acts:
            if act["accountId"] == account_id:
                act_complete = self.delete_act(act["id"])
                complete = complete and act_complete
                if act_complete:
                    print("Deleted: " + act["id"])
                else:
                    print("Failed Delete: " + act["id"])
        return complete

    def get_all_acts_for_account(self, account_id):
        acts = self.get_all_acts()
        filtered_acts = []
        for act in acts:
            if act["accountId"] == account_id:
                filtered_acts.append(act)
        return filtered_acts

    def get_all_acts(self):
        url = f"{self.ghost_host}/api/v1/order"

        payload = {}
        headers = {
            "Authorization": f"Bearer {self.ghost_token}",
        }
        try:
            response = requests.request("GET", url, headers=headers, data=payload)
        except Exception as e:
            print(e)
            return []

        if response.status_code == 200:
            return response.json()["activities"]
        else:
            return []
