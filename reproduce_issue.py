
import re
import ast

def eval_qty_expression(expr):
    """Safely evaluate a simple arithmetic expression for quantity."""
    expr = (expr or "").strip()
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        raise ValueError("Invalid syntax")

    # Define allowed nodes dynamically to avoid AttributeError on newer/older Pythons
    allowed = {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Pow,
        ast.USub,
        ast.UAdd,
    }
    if hasattr(ast, "Constant"):
        allowed.add(ast.Constant)
    if hasattr(ast, "Num"):
        allowed.add(ast.Num)

    def _eval(node):
        if type(node) not in allowed:
            raise ValueError(f"Unsupported expression node: {type(node)}")

        if isinstance(node, ast.Expression):
            return _eval(node.body)

        if hasattr(ast, "Constant") and isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError("Invalid constant")

        if hasattr(ast, "Num") and isinstance(node, ast.Num):
            return float(node.n)
        
        # ... simplifying binary ops for test
        return 0.0

    return float(_eval(tree))

def normalize_key(val):
    return "".join((val or "").lower().split())

def parse_orders_simulation(text):
    print(f"Parsing Text:\n{text}\n")
    item_map = {} # Mock
    orders = []
    unknown = []
    note = ""
    invalid_qty = []
    
    separators = ["จำนวน:", "จำนวน :", "จำนวน", "Qty:", "Qty :", "Qty"]

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        print(f"Processing line: '{line}'")
        if not line: continue
        
        name_part = None
        qty_part = None
        
        for sep in separators:
            idx = line.lower().find(sep.lower())
            if idx != -1:
                name_part = line[:idx].strip()
                qty_remainder = line[idx + len(sep):].strip()
                if qty_remainder.startswith(":") or qty_remainder.startswith("："):
                    qty_remainder = qty_remainder[1:].strip()
                qty_part = qty_remainder
                print(f"  Split by '{sep}': Name='{name_part}', Qty='{qty_part}'")
                break
        
        if name_part and qty_part: # This check handles empty qty logic
            clean_qty = re.sub(r"[^0-9\+\-\*/\(\)\.]", "", qty_part)
            print(f"  Clean Qty: '{clean_qty}' (Valid Chars Only)")
            
            try:
                qty_val = eval_qty_expression(clean_qty)
                print(f"  Eval Result: {qty_val}")
            except Exception as e:
                print(f"  Eval Failed: {e}")
                invalid_qty.append(f"{line} (ระบุจำนวนไม่ถูกต้อง)")
                continue
        else:
            print("  Skipped (name or qty empty)")

    return invalid_qty

text = """
- 1 Bye Heavy จำนวน:
- 2 Green Hug จำนวน: aหก
- 3 Glow Skin จำนวน: 3
"""

result = parse_orders_simulation(text)
print("\nInvaild Qty List:", result)
