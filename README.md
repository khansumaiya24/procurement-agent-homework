# Procurement Agent Homework

## Overview

This project extends the starter procurement demo into a more realistic **LangGraph-based procurement agent**.  
The original starter script was mostly static: quantities were hardcoded, vendor pricing was fixed in code, approval was always requested, and rejection did not properly end the workflow.  

In this homework, I changed the workflow so that the agent can:

- read the requested quantity dynamically from the employee request
- use a tool call to retrieve unit prices
- compare vendor quotes dynamically
- request manager approval only when the total cost is above a threshold
- handle rejection as a proper workflow outcome
- fetch live product data from **DummyJSON**
- keep workflow state using a checkpoint so the process can pause and resume

The result is a procurement agent that behaves more like a real business workflow instead of a fixed script.

---

## Starter Point

Base example used for this assignment:

`demo8.1-purchase-agent.py` from the course repository.

The original demo had these limitations:

- quantity was hardcoded to 50
- vendor prices were hardcoded
- there was no meaningful price lookup decision
- approval interrupt always happened
- rejection did not stop the purchase cleanly
- no external live data source was used

---

## What I Implemented

## Task 1 — Dynamic Quantity via Tool Call

I replaced the fixed pricing logic with a more dynamic approach.

### What was changed
- The employee request is now parsed to extract the quantity.
- A tool named `get_unit_price(vendor: str) -> float` is defined.
- The LLM is bound to the tool and instructed to call it once per vendor.
- The node calculates the total price using:

`total = unit_price × quantity`

### Example
If the request is:

`Order 30 laptops for the sales team`

the system extracts:

- quantity = 30
- item category = laptops

Then it retrieves pricing and calculates totals for each vendor.

### Why this matters
This makes the workflow responsive to user input instead of relying on hardcoded values.

---

## Task 2 — Conditional Interrupt

In the original demo, manager approval was always requested.  
I changed the graph so approval is requested **only when needed**.

### What was changed
After quote comparison, a routing function checks:

- if `best_quote["total"] > 10000` → go to `request_approval`
- otherwise → skip approval and go directly to `submit_purchase_order`

### Why this matters
This reflects a more realistic business process.  
Low-cost purchases can move forward automatically, while high-cost purchases require managerial review.

---

## Task 3 — Graceful Rejection Handling

In the original version, rejection did not behave like a real outcome.  
The workflow continued awkwardly instead of clearly ending in a rejected state.

### What was changed
After `request_approval`, another routing function checks the approval result:

- if approved → continue to `submit_purchase_order`
- if rejected → skip purchase order submission and go directly to `notify_employee`

The employee then receives a rejection message with the rejection reason.

### Example rejection test
The workflow can be resumed with:

```bash
python demo8.1-purchase-agent.py --resume "Rejected — over budget"
