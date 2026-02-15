#!/usr/bin/env python3
import json
import argparse
import os
import sys

def to_snake_case(name):
    return name.lower()

def generate_header_guard(filepath):
    # e.g. inc/vsoc/vsoc.hxx -> VSOC_VSOC_HXX
    parts = filepath.split('/')
    if 'inc' in parts:
        parts = parts[parts.index('inc')+1:]
    guard = "_".join(parts).upper().replace(".", "_").replace("/", "_")
    return f"FIREBALL_{guard}_"

def generate_enum(data):
    lines = []
    name = data.get("name")
    description = data.get("description", "")
    items = data.get("items", [])

    if description:
        lines.append(f"// {description}")
    
    lines.append(f"enum class {name} : uint32_t {{")
    for item in items:
        item_name = item.get("name")
        item_value = item.get("value")
        item_desc = item.get("description", "")
        line = f"  {item_name}"
        if item_value is not None:
            line += f" = {item_value}"
        line += ","
        if item_desc:
            line += f" // {item_desc}"
        lines.append(line)
    lines.append("};")
    return "\n".join(lines)

def generate_struct(data):
    lines = []
    name = data.get("name")
    description = data.get("description", "")
    members = data.get("members", [])
    is_class = data.get("is_class", False)
    
    if description:
        lines.append(f"// {description}")
    
    keyword = "class" if is_class else "struct"
    lines.append(f"{keyword} {name} {{")
    
    for member in members:
        m_type = member.get("type", "byte_count")
        m_name = member.get("name")
        m_desc = member.get("description", "")
        
        # Class members have trailing underscore per coding style
        suffix = "_" if is_class else ""
        line = f"  {m_type} {m_name}{suffix};"
        if m_desc:
            line += f" // {m_desc}"
        lines.append(line)
        
    lines.append("};")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Generate C++ code from JSON")
    parser.add_argument("--input", required=True, help="Input JSON file")
    parser.add_argument("--output", required=True, help="Output header file")
    args = parser.parse_args()

    with open(args.input, "r") as f:
        data = json.load(f)

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    lines = [
        "// AUTO-GENERATED FILE - DO NOT EDIT",
        "#pragma once",
        "",
        "#include <cstdint>",
        "#include \"core/types.hxx\"",
        ""
    ]

    namespace = data.get("namespace")
    if namespace:
        lines.append(f"namespace {namespace} {{")
        lines.append("")

    for item in data.get("definitions", []):
        gen_type = item.get("type")
        if gen_type == "enum":
            lines.append(generate_enum(item))
        elif gen_type == "struct":
            lines.append(generate_struct(item))
        lines.append("")

    if namespace:
        lines.append(f"}} // namespace {namespace}")

    with open(args.output, "w") as f:
        f.write("\n".join(lines))
    
    print(f"Generated {args.output}")

if __name__ == "__main__":
    main()
