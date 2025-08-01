#!/usr/bin/env python3
"""
mininab.py — a minimal YNAB-style CLI tool (v0.9.2)
"""

import argparse
import datetime
import json
import logging
import pathlib
from typing import Dict, Any, List, Optional

# --- Basic Setup ---
DATA = "mininab.json"
LOG_FILE = "mininab.log"

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

############################ Persistence ############################

def load_state() -> Dict[str, Any]:
    logging.info("Loading state from %s", DATA)
    if DATA.exists():
        with DATA.open() as fh:
            return json.load(fh)
    logging.warning("No state file found, starting fresh.")
    return {
        "accounts": {},
        "categories": {},
        "month_summary": {},
        "category_month": {},
        "transactions": []
    }

def save_state(state: Dict[str, Any]) -> None:
    logging.info("Saving state to %s", DATA)
    try:
        DATA.write_text(json.dumps(state, indent=2, default=str))
        logging.info("State saved successfully.")
    except Exception as e:
        logging.error("Failed to save state: %s", e)

############################ Utilities ############################

def parse_month(text: str) -> str:
    text = text.strip()
    for fmt in ["%b %Y", "%B %Y", "%Y-%m", "%Y/%m"]:
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    raise ValueError(f"Unrecognised month format: {text!r}")

def get_cat_entry(state: Dict[str, Any], month: str, cat: str) -> Dict[str, float]:
    cm = state.setdefault("category_month", {}).setdefault(month, {})
    return cm.setdefault(cat, {"budgeted": 0.0, "activity": 0.0, "available": 0.0})

def sorted_categories(state: Dict[str, Any]) -> List[str]:
    cats = state.get("categories", {})
    tree: Dict[Optional[str], List[str]] = {None: []}
    for name, info in cats.items():
        parent = info.get("parent")
        tree.setdefault(parent, []).append(name)
        tree.setdefault(name, [])
    for k in tree:
        tree[k].sort()
    result: List[str] = []
    def visit(parent: Optional[str], indent: str = ""):
        for name in tree.get(parent, []):
            result.append(indent + name)
            visit(name, indent + "  ")
    visit(None)
    return result

############################ Commands ############################

def cmd_acc(state: Dict[str, Any], name: str, typ: str) -> None:
    """Add a bank or credit account"""
    logging.info("Executing cmd_acc: name=%s, type=%s", name, typ)
    if typ not in ("bank", "credit"):
        logging.error("Invalid account type: %s", typ)
        print("Type must be 'bank' or 'credit'.")
        return
    accounts = state.setdefault("accounts", {})
    if name in accounts:
        logging.warning("Account '%s' already exists.", name)
        print(f"Account '{name}' already exists.")
        return
    accounts[name] = {"type": typ}
    logging.info("Account '%s' (%s) added.", name, typ)
    print(f"Account '{name}' ({typ}) added.")

def cmd_cat(state: Dict[str, Any], spec: str) -> None:
    """Add a new category or subcategory using Parent:Child:SubChild..."""
    logging.info("Executing cmd_cat: spec=%s", spec)
    parts = [p.strip() for p in spec.split(':')]
    cats = state.setdefault("categories", {})

    full_path = ""
    parent_path = None
    for i, part in enumerate(parts):
        if i > 0:
            full_path += ":"
        full_path += part

        if full_path not in cats:
            cats[full_path] = {"parent": parent_path}
            logging.info("Category '%s' added.", full_path)
            print(f"Category '{full_path}' added.")
        elif i == len(parts) - 1:
            logging.warning("Category '%s' already exists.", full_path)
            print(f"Category '{full_path}' already exists.")
        
        parent_path = full_path

def cmd_tbb(state: Dict[str, Any], mon: str, amt: float) -> None:
    """Set To-Be-Budgeted for a month"""
    logging.info("Executing cmd_tbb: month=%s, amount=%.2f", mon, amt)
    m = parse_month(mon)
    state.setdefault("month_summary", {}).setdefault(m, {})["ready_to_assign"] = round(amt, 2)
    logging.info("TBB for %s set to %.2f", m, amt)
    print(f"TBB for {m} = {amt:.2f}")

def cmd_bud(state: Dict[str, Any], mon: str, cat: str, amt: float) -> None:
    """Budget amount into a category"""
    logging.info("Executing cmd_bud: month=%s, category=%s, amount=%.2f", mon, cat, amt)
    m = parse_month(mon)
    if cat not in state.get("categories", {}):
        logging.error("Category '%s' not found.", cat)
        print(f"Category '{cat}' not found.")
        return
    entry = get_cat_entry(state, m, cat)
    entry["budgeted"] += amt
    entry["available"] += amt
    state.setdefault("transactions", []).append({"month": m, "account": None, "category": cat, "amount": amt})
    logging.info("Budgeted %.2f to '%s' for %s.", amt, cat, m)
    print(f"Budgeted {amt:.2f} to '{cat}' for {m}.")

def cmd_spend(state: Dict[str, Any], mon: str, acc: str, cat: str, amt: float) -> None:
    """Record spending from an account"""
    logging.info("Executing cmd_spend: month=%s, account=%s, category=%s, amount=%.2f", mon, acc, cat, amt)
    m = parse_month(mon)
    accounts = state.get("accounts", {})
    if acc not in accounts:
        logging.error("Account '%s' not found.", acc)
        print(f"Account '{acc}' not found.")
        return
    if cat not in state.get("categories", {}):
        logging.error("Category '%s' not found.", cat)
        print(f"Category '{cat}' not found.")
        return
    entry = get_cat_entry(state, m, cat)
    entry["activity"] -= amt
    entry["available"] -= amt
    state.setdefault("transactions", []).append({"month": m, "account": acc, "category": cat, "amount": -amt})
    logging.info("Spent %.2f from '%s' -> '%s' for %s.", amt, acc, cat, m)
    print(f"Spent {amt:.2f} from '{acc}' → '{cat}' for {m}.")

def cmd_xfer(state: Dict[str, Any], mon: str, src: str, dst: str, amt: float) -> None:
    """Transfer funds between two accounts (no budget impact)"""
    logging.info("Executing cmd_xfer: month=%s, src=%s, dst=%s, amount=%.2f", mon, src, dst, amt)
    m = parse_month(mon)
    accounts = state.get("accounts", {})
    if src not in accounts or dst not in accounts:
        logging.error("Source or destination account not found for transfer.")
        print(f"Both source and destination accounts must exist.")
        return
    state.setdefault("transactions", []).append({"month": m, "account": src, "category": None, "amount": -amt})
    state.setdefault("transactions", []).append({"month": m, "account": dst, "category": None, "amount": amt})
    logging.info("Transferred %.2f from '%s' to '%s' for %s.", amt, src, dst, m)
    print(f"Transferred {amt:.2f} from '{src}' to '{dst}' for {m}.")

def cmd_roll(state: Dict[str, Any], frm: str, to: str) -> None:
    """Carry balances forward month-to-month"""
    logging.info("Executing cmd_roll: from=%s, to=%s", frm, to)
    f, t = parse_month(frm), parse_month(to)
    cats = state.get("categories", {})
    from_month = state.setdefault("category_month", {}).setdefault(f, {})
    to_month = state.setdefault("category_month", {}).setdefault(t, {})
    overspend = 0.0
    for c in cats:
        prev = from_month.get(c, {"available": 0.0})
        av = prev.get("available", 0.0)
        carry = max(av, 0.0)
        if av < 0.0:
            overspend += -av
        dest = to_month.setdefault(c, {"budgeted": 0.0, "activity": 0.0, "available": 0.0})
        dest["available"] += carry
    msum = state.setdefault("month_summary", {}).setdefault(t, {})
    msum["ready_to_assign"] = msum.get("ready_to_assign", 0.0) - overspend
    logging.info("Rolled forward %s -> %s; overspend %.2f deducted.", f, t, overspend)
    print(f"Rolled forward {f} → {t}; overspend {overspend:.2f} deducted from TBB.")

def cmd_rep(state: Dict[str, Any], mon: str) -> None:
    """Generate report of accounts and categories"""
    logging.info("Executing cmd_rep: month=%s", mon)
    print(f"Report for {m}\n")
    print("Accounts:")
    for name, info in state.get("accounts", {}).items():
        print(f"  {name} ({info['type']})")
    print()
    cats = state.get("categories", {})
    cm = state.setdefault("category_month", {}).setdefault(m, {})
    for c in cats:
        cm.setdefault(c, {"budgeted": 0.0, "activity": 0.0, "available": 0.0})
    print("Categories:")
    print("Category               Budg     Actv     Avail")
    print("" + "-"*50)
    total_bud = 0.0
    for c in sorted_categories(state):
        val = cm[c.lstrip()]
        print(f"{c[:20]:20} {val['budgeted']:7.2f} {val['activity']:8.2f} {val['available']:8.2f}")
        total_bud += val['budgeted']
    tbb = state.get("month_summary", {}).get(m, {}).get("ready_to_assign", 0.0)
    print("" + "-"*50)
    print(f"Remaining TBB: {tbb - total_bud:.2f}")

def cmd_show(state: Dict[str, Any]) -> None:
    """Print summary of accounts, categories, and month TBB"""
    logging.info("Executing cmd_show")
    print("Accounts:")
    for name, info in state.get("accounts", {}).items():
        print(f"  {name}: {info['type']}")
    print("\nCategories:")
    for c in sorted_categories(state):
        print(f"  {c}")
    print("\nMonth Summaries:")
    for m, info in sorted(state.get("month_summary", {}).items()):
        print(f"  {m}: {info.get('ready_to_assign', 0.0):.2f}")

############################ CLI wiring ############################

def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MiniYNAB command-line clone")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mapping = [
        ("acc", ["name", "type"]),
        ("cat", ["spec"]),
        ("tbb", ["month", "amount"]),
        ("bud", ["month", "category", "amount"]),
        ("spend", ["month", "account", "category", "amount"]),
        ("xfer", ["month", "src", "dst", "amount"]),
        ("roll-forward", ["from", "to"]),
        ("rep", ["month"]),
        ("show", [])
    ]
    for name, params in mapping:
        cmd_parser = sub.add_parser(name)
        for arg in params:
            kwargs = {"type": float} if arg in ("amount",) else {}
            cmd_parser.add_argument(arg, **kwargs)
    
    args = parser.parse_args(argv)
    
    logging.info("CLI command: %s with args %s", args.cmd, vars(args))
    
    state = load_state()
    cmd = args.cmd.replace("-", "_")
    fn = globals().get(f"cmd_{cmd}")
    
    if fn:
        try:
            fn(state, **{k: v for k, v in vars(args).items() if k != "cmd"})
            if cmd in ("acc", "cat", "tbb", "bud", "spend", "xfer", "roll_forward"):
                save_state(state)
        except Exception as e:
            logging.exception("An error occurred during command execution: %s", e)
            print(f"An error occurred: {e}")
    else:
        logging.error("Command not found: %s", args.cmd)

if __name__ == "__main__":
    main()