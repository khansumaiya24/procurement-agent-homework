
"""
Homework: Procurement Agent
Tasks solved:
1) Dynamic quantity via tool call
2) Conditional interrupt (> €10,000 only)
3) Graceful rejection path
4) Live data from dummyjson.com

Run:
    python demo8.1-purchase-agent.py
    python demo8.1-purchase-agent.py --resume
    python demo8.1-purchase-agent.py --resume "Rejected — over budget"
"""

import json
import os
import re
import sqlite3
import sys
import time
from typing import TypedDict
from urllib.request import urlopen, Request

from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

APPROVAL_THRESHOLD = 10_000
DEFAULT_UNIT_PRICE = 999.0


# ──────────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────────
class ProcurementState(TypedDict, total=False):
    request: str
    quantity: int
    item_category: str
    vendors: list[dict]
    quotes: list[dict]
    best_quote: dict
    approval_status: str
    rejection_reason: str
    po_number: str
    notification: str


# ──────────────────────────────────────────────────────────────────────────────
# LLM
# ──────────────────────────────────────────────────────────────────────────────
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def extract_quantity(request_text: str) -> int:
    """
    Extract the first integer from a request like:
    'Order 30 laptops for the sales team'
    """
    match = re.search(r"(\d+)", request_text)
    return int(match.group(1)) if match else 1


def extract_item_category(request_text: str) -> str:
    text = request_text.lower()
    if "smartphone" in text or "phone" in text:
        return "smartphones"
    return "laptops"
def parse_shipping_days(shipping_info: str) -> int:
    if not shipping_info:
        return 999

    text = shipping_info.lower().strip()

    if "overnight" in text:
        return 1
    if "same day" in text:
        return 0

    number_match = re.search(r"(\d+)", text)
    number = int(number_match.group(1)) if number_match else None

    if number is None:
        return 999

    if "day" in text:
        return number
    if "week" in text:
        return number * 7
    if "month" in text:
        return number * 30

    return 999



def fetch_products_by_category(category: str) -> list[dict]:
    url = f"https://dummyjson.com/products/category/{category}"

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urlopen(req) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return payload.get("products", [])

def choose_best_product(vendor: str, category: str) -> dict:
    """
    Pull live products from DummyJSON and choose the cheapest product that:
    - is in stock
    - is not out of stock
    - can ship within 2 weeks

    Prefer products matching the vendor brand name.
    If no vendor-matching product exists, fall back to the cheapest eligible product.
    """
    try:
        products = fetch_products_by_category(category)

        eligible = []
        for product in products:
            stock = int(product.get("stock", 0) or 0)
            availability = str(product.get("availabilityStatus", "")).lower()
            shipping_information = str(product.get("shippingInformation", ""))
            shipping_days = parse_shipping_days(shipping_information)
            title = str(product.get("title", ""))

            is_in_stock = stock > 0 and "out of stock" not in availability
            within_two_weeks = shipping_days <= 14

            if is_in_stock and within_two_weeks:
                eligible.append(product)

        if not eligible:
            print(
                f" [warning] No eligible {category} found from DummyJSON for vendor {vendor}. "
                f"Using fallback default."
            )
            return {
                "product_name": f"Fallback {category[:-1].title()}",
                "unit_price": DEFAULT_UNIT_PRICE,
                "delivery_days": 14,
                "shipping_information": "Fallback default: ships in 2 weeks",
                "availability_status": "Assumed available",
                "stock": 999,
                "source": "fallback",
            }

        vendor_lower = vendor.lower()

        brand_keywords = {
            "dell": ["dell"],
            "lenovo": ["lenovo"],
            "hp": ["hp", "hewlett"],
        }

        keywords = brand_keywords.get(vendor_lower, [vendor_lower])

        vendor_matched = []
        for product in eligible:
            title_lower = str(product.get("title", "")).lower()
            if any(keyword in title_lower for keyword in keywords):
                vendor_matched.append(product)

        pool = vendor_matched if vendor_matched else eligible
        cheapest = min(pool, key=lambda p: float(p.get("price", DEFAULT_UNIT_PRICE)))

        return {
            "product_name": cheapest.get("title", f"Unknown {category[:-1].title()}"),
            "unit_price": float(cheapest.get("price", DEFAULT_UNIT_PRICE)),
            "delivery_days": parse_shipping_days(cheapest.get("shippingInformation", "")),
            "shipping_information": cheapest.get("shippingInformation", "Unknown shipping time"),
            "availability_status": cheapest.get("availabilityStatus", "Unknown"),
            "stock": int(cheapest.get("stock", 0) or 0),
            "source": "dummyjson-vendor-match" if vendor_matched else "dummyjson-fallback-cheapest",
        }

    except Exception as e:
        print(f" [warning] API lookup failed for {vendor}/{category}: {e}. Using fallback default.")
        return {
            "product_name": f"Fallback {category[:-1].title()}",
            "unit_price": DEFAULT_UNIT_PRICE,
            "delivery_days": 14,
            "shipping_information": "Fallback default: ships in 2 weeks",
            "availability_status": "Assumed available",
            "stock": 999,
            "source": "fallback",
        }



# ──────────────────────────────────────────────────────────────────────────────
# Tool
# ──────────────────────────────────────────────────────────────────────────────
@tool
def get_unit_price(vendor: str) -> float:
    """
    Get the current unit price for a vendor.
    Prefer a product matching the vendor brand name.
    Fall back to the cheapest eligible laptop if needed.
    """
    product = choose_best_product(vendor, "laptops")
    return float(product["unit_price"])

# ──────────────────────────────────────────────────────────────────────────────
# Node functions
# ──────────────────────────────────────────────────────────────────────────────
def lookup_vendors(state: ProcurementState) -> dict:
    """Step 1: Look up approved vendors and parse request details."""
    print("\n[Step 1] Looking up approved vendors and parsing request...")
    time.sleep(0.5)

    quantity = extract_quantity(state["request"])
    item_category = extract_item_category(state["request"])

    vendors = [
        {"name": "Dell", "id": "V-001", "category": item_category, "rating": 4.5},
        {"name": "Lenovo", "id": "V-002", "category": item_category, "rating": 4.3},
        {"name": "HP", "id": "V-003", "category": item_category, "rating": 4.1},
    ]

    print(f" Quantity parsed from request: {quantity}")
    print(f" Item category: {item_category}")
    for v in vendors:
        print(f" Found vendor: {v['name']} (rating {v['rating']})")

    return {
        "quantity": quantity,
        "item_category": item_category,
        "vendors": vendors,
    }


def fetch_pricing(state: ProcurementState) -> dict:
    """
    Step 2: Use tool calling.
    The LLM is bound to get_unit_price and asked to call it once per vendor.
    Then we build quote objects dynamically using the parsed quantity.
    """
    print("\n[Step 2] Fetching pricing via tool calls...")
    time.sleep(0.5)

    quantity = state["quantity"]
    item_category = state["item_category"]
    vendor_names = [v["name"] for v in state["vendors"]]

    llm_with_tools = llm.bind_tools([get_unit_price])

    # Ask the model to call the tool once per vendor
    prompt = f"""
You are a procurement assistant.

Employee request: "{state['request']}"
Parsed quantity: {quantity}
Approved vendors: {vendor_names}

Call the tool get_unit_price exactly once for each vendor in the list.
Do not answer normally. Use tool calls only.
"""

    response = llm_with_tools.invoke(prompt)
    tool_calls = getattr(response, "tool_calls", []) or []

    # Fallback in case the model misses a vendor
    already_called = {
        tc["args"]["vendor"]
        for tc in tool_calls
        if tc.get("name") == "get_unit_price" and "vendor" in tc.get("args", {})
    }

    for vendor_name in vendor_names:
        if vendor_name not in already_called:
            tool_calls.append(
                {"name": "get_unit_price", "args": {"vendor": vendor_name}}
            )

    quotes = []

    for tc in tool_calls:
        if tc.get("name") != "get_unit_price":
            continue

        vendor = tc["args"]["vendor"]

        # Task 1: tool call gets unit price
        unit_price = float(get_unit_price.invoke({"vendor": vendor}))

        # Task 4: fetch the actual selected product info to pass forward
        product_info = choose_best_product(vendor, item_category)

        total = unit_price * quantity

        quote = {
            "vendor": vendor,
            "product_name": product_info["product_name"],
            "unit_price": unit_price,
            "total": total,
            "delivery_days": product_info["delivery_days"],
            "shipping_information": product_info["shipping_information"],
            "availability_status": product_info["availability_status"],
            "stock": product_info["stock"],
            "category": item_category,
            "source": product_info["source"],
        }
        quotes.append(quote)

    # De-duplicate in case model called more than once
    deduped_quotes = {}
    for q in quotes:
        deduped_quotes[q["vendor"]] = q
    quotes = list(deduped_quotes.values())

    for q in quotes:
        print(
            f" {q['vendor']}: {q['product_name']} | "
            f"€{q['unit_price']}/unit x {quantity} = €{q['total']:,.2f} "
            f"({q['delivery_days']} day delivery)"
        )

    return {"quotes": quotes}


def compare_quotes(state: ProcurementState) -> dict:
    """Step 3: Compare quotes and pick the best one."""
    print("\n[Step 3] Comparing quotes...")
    time.sleep(0.5)

    best = min(state["quotes"], key=lambda q: q["total"])

    print(f" Best quote: {best['vendor']} at €{best['total']:,.2f}")
    print(f" Product: {best['product_name']}")

    return {"best_quote": best}


def approval_needed_router(state: ProcurementState) -> str:
    """
    Task 2:
    If best quote total > €10,000 -> request approval
    else -> skip approval and go directly to submit PO
    """
    total = float(state["best_quote"]["total"])
    if total > APPROVAL_THRESHOLD:
        return "request_approval"
    return "submit_purchase_order"


def request_approval(state: ProcurementState) -> dict:
    """Step 4: Human approval only for expensive orders."""
    best = state["best_quote"]

    print("\n[Step 4] Order exceeds €10,000 — manager approval required!")
    print(" Sending approval request to manager...")

    amount_str = f"€{best['total']:,.2f}"
    delivery_str = f"{best['delivery_days']} business days"

    print(" ┌──────────────────────────────────────────────────────────┐")
    print(" │ APPROVAL NEEDED                                          │")
    print(f" │ Vendor: {best['vendor']:<49}│")
    print(f" │ Product: {best['product_name']:<48}│")
    print(f" │ Amount: {amount_str:<49}│")
    print(f" │ Items: {state['quantity']} {best['category']:<48}│")
    print(f" │ Delivery: {delivery_str:<47}│")
    print(" └──────────────────────────────────────────────────────────┘")

    decision = interrupt(
        {
            "message": (
                f"Approve purchase of {state['quantity']} {best['category']} "
                f"({best['product_name']}) from {best['vendor']} "
                f"for €{best['total']:,.2f}?"
            ),
            "vendor": best["vendor"],
            "product_name": best["product_name"],
            "amount": best["total"],
            "quantity": state["quantity"],
        }
    )

    print(f"\n[Step 4] Manager responded: {decision}")
    return {"approval_status": str(decision)}


def approval_result_router(state: ProcurementState) -> str:
    """
    Task 3:
    If approved -> submit purchase order
    If rejected -> skip PO and go straight to notify_employee
    """
    status = state.get("approval_status", "").lower()
    if "reject" in status:
        return "notify_employee"
    return "submit_purchase_order"


def submit_purchase_order(state: ProcurementState) -> dict:
    """Step 5: Submit PO if approved or if approval was not needed."""
    print("\n[Step 5] Submitting purchase order to ERP system...")
    time.sleep(0.5)

    po_number = "PO-2026-00342"

    print(f" Purchase order created: {po_number}")
    print(f" Vendor: {state['best_quote']['vendor']}")
    print(f" Product: {state['best_quote']['product_name']}")
    print(f" Amount: €{state['best_quote']['total']:,.2f}")

    return {"po_number": po_number}


def notify_employee(state: ProcurementState) -> dict:
    """Step 6: Notify employee for either approval or rejection."""
    print("\n[Step 6] Notifying employee...")

    best = state["best_quote"]

    if "reject" in state.get("approval_status", "").lower():
        rejection_reason = state.get("approval_status", "Rejected by manager")
        prompt = (
            f"Write a brief, professional notification (2-3 sentences) to an employee "
            f"that their purchase request for {state['quantity']} {best['category']} "
            f"({best['product_name']}) was rejected by the manager. "
            f"Reason: {rejection_reason}. "
            f"Be empathetic but concise."
        )
    else:
        prompt = (
            f"Write a brief, professional notification (2-3 sentences) to an employee "
            f"that their purchase request has been approved and processed. "
            f"Details: {state['quantity']} {best['category']} "
            f"({best['product_name']}) from {best['vendor']}, "
            f"€{best['total']:,.2f}, PO number {state['po_number']}, "
            f"delivery in {best['delivery_days']} business days."
        )

    response = llm.invoke(prompt)
    notification = response.content

    print(" Employee notification sent:")
    print(f' "{notification}"')

    return {"notification": notification}


# ──────────────────────────────────────────────────────────────────────────────
# Build graph
# ──────────────────────────────────────────────────────────────────────────────
builder = StateGraph(ProcurementState)

builder.add_node("lookup_vendors", lookup_vendors)
builder.add_node("fetch_pricing", fetch_pricing)
builder.add_node("compare_quotes", compare_quotes)
builder.add_node("request_approval", request_approval)
builder.add_node("submit_purchase_order", submit_purchase_order)
builder.add_node("notify_employee", notify_employee)

builder.add_edge(START, "lookup_vendors")
builder.add_edge("lookup_vendors", "fetch_pricing")
builder.add_edge("fetch_pricing", "compare_quotes")

# Task 2: conditional interrupt
builder.add_conditional_edges(
    "compare_quotes",
    approval_needed_router,
    {
        "request_approval": "request_approval",
        "submit_purchase_order": "submit_purchase_order",
    },
)

# Task 3: handle rejection gracefully
builder.add_conditional_edges(
    "request_approval",
    approval_result_router,
    {
        "submit_purchase_order": "submit_purchase_order",
        "notify_employee": "notify_employee",
    },
)

builder.add_edge("submit_purchase_order", "notify_employee")
builder.add_edge("notify_employee", END)


# ──────────────────────────────────────────────────────────────────────────────
# Checkpointer
# ──────────────────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "procurement_checkpoints.db",
)

THREAD_ID = "procurement-thread-1"
config = {"configurable": {"thread_id": THREAD_ID}}


# ──────────────────────────────────────────────────────────────────────────────
# Main helpers
# ──────────────────────────────────────────────────────────────────────────────
def run_first_invocation(graph):
    print("=" * 60)
    print(" FIRST INVOCATION — Employee submits purchase request")
    print("=" * 60)

    request_text = "Order 30 laptops for the sales team"
    print(f'\nEmployee request: "{request_text}"')

    result = graph.invoke({"request": request_text}, config)

    # If interrupted, approval is waiting
    if "__interrupt__" in result:
        print("\n" + "=" * 60)
        print("AGENT SUSPENDED — waiting for manager approval")
        print("=" * 60)
        print("\n The agent process can now exit completely.")
        print(" All state is frozen in SQLite.")
        print(f" Checkpoint DB: {DB_PATH}")
        print(f" Thread ID: {THREAD_ID}")
        print("\n To resume, run:")
        print(f" python {os.path.basename(__file__)} --resume")
        print(f' or python {os.path.basename(__file__)} --resume "Rejected — over budget"\n')
    else:
        print("\n" + "=" * 60)
        print("PROCUREMENT COMPLETE (approval not needed)")
        print("=" * 60)
        print(f"\n PO Number: {result.get('po_number', 'N/A')}")
        print(f" Vendor: {result.get('best_quote', {}).get('vendor', 'N/A')}")
        print(f" Product: {result.get('best_quote', {}).get('product_name', 'N/A')}")
        print(f" Total: €{result.get('best_quote', {}).get('total', 0):,.2f}")
        print()


def run_second_invocation(graph, resume_value: str):
    print("=" * 60)
    print(" SECOND INVOCATION — Manager responds")
    print("=" * 60)

    saved_state = graph.get_state(config)
    if not saved_state or not saved_state.values:
        print("\nNo saved state found! Run without --resume first.")
        return

    print("\nLoading state from checkpoint...")
    print(f" ✓ Request: {saved_state.values.get('request', 'N/A')}")
    print(f" ✓ Quantity: {saved_state.values.get('quantity', 'N/A')}")
    print(f" ✓ Vendors found: {len(saved_state.values.get('vendors', []))}")
    print(f" ✓ Quotes received: {len(saved_state.values.get('quotes', []))}")

    best = saved_state.values.get("best_quote", {})
    print(f" ✓ Best quote: {best.get('vendor', 'N/A')} at €{best.get('total', 0):,.2f}")
    print(f" ✓ Product: {best.get('product_name', 'N/A')}")

    print("\n Steps 1-3 are NOT re-executed — their output is in the checkpoint!\n")

    print(f'Manager response: "{resume_value}"')
    time.sleep(0.5)

    result = graph.invoke(Command(resume=resume_value), config)

    print("\n" + "=" * 60)
    print("PROCUREMENT COMPLETE")
    print("=" * 60)
    print(f"\n PO Number: {result.get('po_number', 'N/A')}")
    print(f" Vendor: {result.get('best_quote', {}).get('vendor', 'N/A')}")
    print(f" Product: {result.get('best_quote', {}).get('product_name', 'N/A')}")
    print(f" Total: €{result.get('best_quote', {}).get('total', 0):,.2f}")
    print(f" Approval: {result.get('approval_status', 'N/A')}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    resume_mode = "--resume" in sys.argv

    if not resume_mode and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("(Cleaned up old checkpoint DB)")

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = builder.compile(checkpointer=checkpointer)

    try:
        if resume_mode:
            resume_index = sys.argv.index("--resume")
            if len(sys.argv) > resume_index + 1:
                resume_value = sys.argv[resume_index + 1]
            else:
                resume_value = "Approved — go ahead with the purchase."
            run_second_invocation(graph, resume_value)
        else:
            run_first_invocation(graph)
    finally:
        conn.close()