# Procurement Agent Homework

## Overview

This project extends the starter `demo8.1-purchase-agent.py` into a more realistic and dynamic procurement agent using LangGraph.

The original starter demo was mostly static:
- quantity was hardcoded
- vendor prices were hardcoded
- approval always happened
- rejection was not handled as a proper final outcome
- no live external product data was used

In this homework, I redesigned the workflow so that it can:
- extract quantity from the employee request
- use a tool call to get vendor pricing
- compare quotes dynamically
- request approval only when needed
- handle rejection properly
- fetch live product and price data from DummyJSON
- pause and resume execution using checkpointing

This makes the workflow closer to a real procurement process.

---

## Starter Point

Base example used for the assignment:

`demo8.1-purchase-agent.py`

The original demo already showed the idea of a procurement workflow, but it did not make real dynamic decisions.

---

## Implemented Tasks

### Task 1 — Dynamic Quantity via Tool Call

**Goal:** Replace the hardcoded pricing logic with a dynamic, tool-based pricing approach.

**What I changed**
- The employee request string is parsed to extract the requested quantity.
- A tool named `get_unit_price(vendor: str) -> float` is defined.
- The LLM is bound to this tool.
- The LLM calls the tool for each vendor.
- Total cost is calculated dynamically using:

```python
total = unit_price * quantity
Example
For the request:

Order 30 laptops for the sales team

  the system extracts:

       quantity = 30
       category = laptops

Then it retrieves pricing for the available vendors and calculates total price per vendor.

#Why this matters

This removes the hardcoded quantity and makes the workflow respond to the actual purchase request.

###Task 2 — Conditional Interrupt

Goal: Trigger manager approval only when the order is expensive.

What I changed

After compare_quotes, I added a routing function using LangGraph conditional edges.

The routing checks:

  if best_quote["total"] > 10000 → go to request_approval
  otherwise → skip approval and go directly to submit_purchase_order

Why this matters

This reflects a more realistic business rule:

    smaller purchases can be processed automatically
     larger purchases need manager approval

Result

In my test run, the total exceeded €10,000, so the graph correctly routed to the approval step.

Task 3 — Handle Rejection Gracefully

Goal: Make rejection a proper workflow outcome instead of letting the process continue awkwardly.

What I changed

After request_approval, I added another routing function.

The logic is:

if approved → continue to submit_purchase_order
if rejected → skip submit_purchase_order and go directly to notify_employee

The notification node handles both:

approval message
rejection message

Rejection test

The rejection path can be tested with:

python demo8.1-purchase-agent.py --resume "Rejected — over budget"

Result

In the rejection case:

no purchase order is created
the employee receives a rejection message
the final state clearly shows rejection

Why this matters

This creates a clean and realistic end state for rejected purchase requests.

Task 4 — Real Data from DummyJSON

Goal: Replace hardcoded price values with live data from a real API.

API used

https://dummyjson.com/products/category/laptops

What I changed

The agent fetches live product data from DummyJSON.
It checks available products in the laptops category.
It filters products based on stock and delivery time.
It prefers products available within 2 weeks.
It selects the cheapest eligible product.
It passes the selected product name forward in the workflow.

Product selection logic

The code checks:

product price
product stock
availability status
shipping information

If a product is available, in stock, and can be delivered within 2 weeks, it is considered eligible.

Vendor-aware improvement

To make results more realistic, I improved the selection logic so that:

Dell first tries to match Dell products
Lenovo first tries to match Lenovo products
HP first tries to match HP products
if no vendor-matching product is found, the system falls back to the cheapest eligible product

This avoids unrealistic pairings such as:

Vendor: Dell
Product: Lenovo Yoga 920

Fallback handling

If:

the API request fails, or
no suitable product is found

the code uses a sensible fallback price and logs a warning instead of crashing.

Why this matters

This task makes the procurement agent more realistic because it now works with live external data instead of fixed values written directly in the code.

Final Workflow

The final workflow is:

lookup_vendors
Parse the employee request, extract quantity, determine category, and define vendors
fetch_pricing
Use tool calling and live data to build vendor quotes
compare_quotes
Compare all quote totals and select the best quote
request_approval (only if total > €10,000)
Pause the process and wait for manager input
submit_purchase_order
Submit a PO if the request is approved or if approval is not required
notify_employee
Inform the employee about the final outcome
State Persistence and Resume Logic

The workflow uses SQLite checkpointing.

This allows the graph to:

stop when manager approval is needed
save the current state
resume later without re-running earlier steps

This is useful because real business approval processes are often asynchronous.

First run

python demo8.1-purchase-agent.py

Resume with approval

python demo8.1-purchase-agent.py --resume

Resume with rejection

python demo8.1-purchase-agent.py --resume "Rejected — over budget"

Why this matters

This demonstrates a proper human-in-the-loop workflow using LangGraph interrupts and checkpointing.

Technologies Used
Python
 LangGraph
  LangChain
   Gemini via langchain-google-genai
     SQLite checkpointing
        DummyJSON API

How to Run
1. Create and activate a virtual environment
macOS / Linux
python3 -m venv venv
source venv/bin/activate

2. Install dependencies
pip install -U langgraph langchain langchain-google-genai langgraph-checkpoint-sqlite

3. Set API key
export GOOGLE_API_KEY="your_api_key_here"

4. Run the workflow
python demo8.1-purchase-agent.py

5. Resume after approval interrupt
python demo8.1-purchase-agent.py --resume

6. Test rejection path
python demo8.1-purchase-agent.py --resume "Rejected — over budget"

Example Run Summary

Example request

Order 30 laptops for the sales team

Example behavior

quantity is extracted as 30
vendor pricing is calculated dynamically
real laptop data is fetched from DummyJSON
quotes are compared
best quote is selected
approval is requested because total exceeds €10,000
after approval, PO is created
after rejection, PO is skipped and employee is notified

#What I Learned

Through this homework I learned how to:

turn a fixed script into a dynamic agent workflow
bind tools to an LLM
use LangGraph conditional routing
implement human approval with interrupts
resume execution using checkpoint persistence
integrate live API data into agent logic
design cleaner approval and rejection outcomes

This assignment helped me understand how agent workflows can be used for practical procurement automation.

