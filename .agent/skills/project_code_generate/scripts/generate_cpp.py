#!/usr/bin/env python3
"""
WIT to C++ Header Generator (using wasm-tools) - Package-level version

This script parses an entire WIT package directory and generates C++ headers
for all interfaces.

Usage:
    python wit_to_cpp.py wit/ inc/gen
"""

import json
import subprocess
import sys
import os
import re
from pathlib import Path


def to_snake_case(name):
    """Convert kebab-case or PascalCase to snake_case."""
    result = name.replace("-", "_")
    snake = []
    for i, char in enumerate(result):
        if char.isupper() and i > 0:
            snake.append("_")
        snake.append(char.lower())
    return "".join(snake)


def to_upper_snake_case(name):
    """Convert to UPPER_SNAKE_CASE."""
    return to_snake_case(name).upper()


def map_type(wit_type, types_list):
    """Map WIT types to C++ types."""
    if isinstance(wit_type, str):
        mapping = {
            "u8": "uint8_t",
            "u16": "uint16_t",
            "u32": "uint32_t",
            "u64": "uint64_t",
            "s8": "int8_t",
            "s16": "int16_t",
            "s32": "int32_t",
            "s64": "int64_t",
            "bool": "bool",
            "string": "std::string_view",
            "char": "char",
            "f32": "float",
            "f64": "double"
        }
        return mapping.get(wit_type, to_snake_case(wit_type))
    elif isinstance(wit_type, int):
        # Reference to a type definition
        type_def = types_list[wit_type]
        if type_def.get('name'):
            return to_snake_case(type_def['name'])
        
        # Anonymous type
        kind = type_def.get('kind')
        if isinstance(kind, dict):
            if 'result' in kind:
                res = kind['result']
                ok_type = "void"
                err_type = "void"
                if 'ok' in res and res['ok'] is not None:
                    ok_type = map_type(res['ok'], types_list)
                if 'err' in res and res['err'] is not None:
                    err_type = map_type(res['err'], types_list)
                return f"std::expected<{ok_type}, {err_type}>"
            elif 'option' in kind:
                opt = kind['option']
                val_type = map_type(opt, types_list)
                return f"std::optional<{val_type}>"
            elif 'list' in kind:
                l = kind['list']
                val_type = map_type(l, types_list)
                if val_type == "uint8_t":
                    return "binary_view"
                return f"std::span<{val_type}>"
            elif 'tuple' in kind:
                t = kind['tuple']
                types = [map_type(x, types_list) for x in t]
                return f"std::tuple<{', '.join(types)}>"
        
        # Resource reference or other anonymous type
        if kind == 'resource':
            return f"{to_snake_case(type_def['name'])}*"
            
        return "uintptr_t" # Fallback to integer address instead of void*
    elif isinstance(wit_type, dict):
        if 'type' in wit_type:
            return map_type(wit_type['type'], types_list)
        return "uintptr_t"
    return "uintptr_t"

def parse_wit_package(wit_dir):
    """Parse entire WIT package directory using wasm-tools."""
    try:
        result = subprocess.run(
            ["wasm-tools", "component", "wit", wit_dir, "--json"],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error: wasm-tools failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: wasm-tools not found", file=sys.stderr)
        sys.exit(1)


def generate_cpp(wit_json, output_dir):
    """Generate C++ headers from wasm-tools JSON."""
    os.makedirs(output_dir, exist_ok=True)
    
    interfaces = wit_json.get("interfaces", [])
    types_list = wit_json.get("types", [])
    
    for iface_idx, iface in enumerate(interfaces):
        iface_name = iface['name']
        output_path = Path(output_dir) / f"{to_snake_case(iface_name)}.hxx"
        
        with open(output_path, 'w') as f:
            # Header
            f.write("/**\n * Auto-generated from WIT. Do not edit.\n */\n")
            f.write("#pragma once\n\n")
            f.write("#include <fireball_types.hxx>\n")
            if iface_name != "types":
                f.write("#include <gen/types.hxx>\n") 
            f.write("#include <fireball_config.hxx>\n")
            f.write("#include <cstdint>\n")
            f.write("#include <string_view>\n")
            f.write("#include <expected>\n")
            f.write("#include <optional>\n")
            f.write("#include <tuple>\n\n")
            f.write("namespace fireball {\n\n")
            
            # --- Types ---
            type_indices = iface.get('types', {})
            for type_name, type_idx in type_indices.items():
                if type_idx >= len(types_list):
                    continue
                
                type_def = types_list[type_idx]
                kind = type_def.get('kind', {})
                docs = type_def.get('docs', {}).get('contents', '')
                
                if isinstance(kind, str) and kind == "resource":
                     # Resources handled later as classes
                     continue

                # Write docs
                if docs:
                    f.write("/**\n")
                    for line in docs.split('\n'):
                        f.write(f" * {line}\n")
                    f.write(" */\n")
                
                # Type alias
                if 'type' in kind:
                    base_type = kind['type']
                    if isinstance(base_type, str):
                        cpp_type = map_type(base_type, types_list)
                    else:
                        cpp_type = to_snake_case(types_list[base_type]['name'])
                    
                    alias_name = to_snake_case(type_name)
                    if alias_name != cpp_type:
                        f.write(f"using {alias_name} = {cpp_type};\n\n")
                
                # Enum
                elif 'enum' in kind:
                    cases = kind['enum'].get('cases', [])
                    f.write(f"enum class {to_snake_case(type_name)} : uint8_t {{\n")
                    for case in cases:
                        f.write(f"  {to_upper_snake_case(case['name'])},\n")
                    f.write("};\n\n")
                
                # Record
                elif 'record' in kind:
                    # Check for @bitfield annotation in docs
                    bitfield_spec = None
                    if docs:
                        for line in docs.split('\n'):
                            if '@bitfield' in line:
                                bitfield_spec = line.split('@bitfield')[1].strip()
                                break
                    
                    if bitfield_spec:
                        # Parse bitfield: scope:u8:0-7, key:u24:8-31, value:u32:32-63
                        f.write(f"struct {to_snake_case(type_name)} {{\n")
                        parts = [p.strip() for p in bitfield_spec.split(',')]
                        total_bits = 0
                        for part in parts:
                            # part format: name:type:range
                            name, btype, brange = part.split(':')
                            start, end = map(int, brange.split('-'))
                            width = end - start + 1
                            f.write(f"  uint64_t {to_snake_case(name)} : {width};  // Bits {brange}\n")
                            total_bits += width
                        f.write("};\n")
                        f.write(f"static_assert(sizeof({to_snake_case(type_name)}) == {total_bits // 8}, \"{type_name} size mismatch\");\n\n")
                    else:
                        fields = kind['record'].get('fields', [])
                        f.write(f"struct {to_snake_case(type_name)} {{\n")
                        for field in fields:
                            field_name = to_snake_case(field['name'])
                            field_type = map_type(field['type'], types_list)
                            f.write(f"  {field_type} {field_name};\n")
                        f.write("};\n\n")

            # --- Resources (Classes) ---
            # Identify resources owned by this interface
            resources = []
            for idx, typedef in enumerate(types_list):
                if typedef.get('owner') and typedef.get('owner').get('interface') == iface_idx and typedef.get('kind') == 'resource':
                    resources.append((idx, typedef))

            # --- Functions and Methods ---
            # Pre-process functions to categorize them by resource owner
            resource_methods = {res_idx: [] for res_idx, _ in resources}
            free_functions = []

            iface_funcs = iface.get('functions', {})
            for func_name, func_def in iface_funcs.items():
                # Check if it's a method: name format "[method]resource.name" or "[static]resource.name"
                match = re.match(r'\[(method|static)\]([a-zA-Z0-9\-]+)\.(.+)', func_name)
                if match:
                    kind, res_name, method_name = match.groups()
                    # Find resource index by name
                    target_res_idx = -1
                    for idx, typedef in resources:
                        if typedef['name'] == res_name:
                            target_res_idx = idx
                            break
                    
                    if target_res_idx != -1:
                        resource_methods[target_res_idx].append((method_name, func_def, kind == 'static'))
                    else:
                        pass # Warning?
                else:
                    free_functions.append((func_name, func_def))

            for res_idx, res_def in resources:
                res_name = res_def.get('name')
                docs = res_def.get('docs', {}).get('contents', '')
                
                if docs:
                    f.write("/**\n")
                    for line in docs.split('\n'):
                        f.write(f" * {line}\n")
                    f.write(" */\n")
                
                f.write(f"class {to_snake_case(res_name)} {{\n")
                f.write("public:\n")
                
                # Constructor/Destructor
                f.write(f"  {to_snake_case(res_name)}() = default;\n")
                f.write(f"  ~{to_snake_case(res_name)}() = default;\n\n")

                # Methods
                methods = resource_methods.get(res_idx, [])
                for method_name, func_def, is_static in methods:
                    params = func_def.get('params', [])
                    results = func_def.get('results', [])
                    if method_name == "lookup" or method_name == "collect":
                         pass # Debug verified

                    docs = func_def.get('docs', {}).get('contents', '')

                    if docs:
                        f.write("  /**\n")
                        for line in docs.split('\n'):
                            f.write(f"   * {line}\n")
                        f.write("   */\n")

                    # Return type
                    ret_type = "void"
                    if 'result' in func_def:
                        # Single return value
                        ret_type = map_type(func_def['result'], types_list)
                    elif 'results' in func_def:
                        results = func_def['results']
                        if len(results) == 1:
                            ret_type = map_type(results[0]['type'], types_list)
                        elif len(results) > 1:
                            types = [map_type(r['type'], types_list) for r in results]
                            ret_type = f"std::tuple<{', '.join(types)}>"

                    # Params
                    param_strs = []
                    for p in params:
                        p_name = to_snake_case(p['name'])
                        if p_name == "self":
                            continue
                        p_type = map_type(p['type'], types_list)
                        param_strs.append(f"{p_type} {p_name}")

                    prefix = "static " if is_static else ""
                    f.write(f"  {prefix}{ret_type} {to_snake_case(method_name)}({', '.join(param_strs)}) noexcept;\n\n")
                
                f.write("};\n\n")

            # --- Functions (Free-standing) ---
            for func_name, func_def in free_functions:
                params = func_def.get('params', [])
                results = func_def.get('results', [])
                docs = func_def.get('docs', {}).get('contents', '')
                
                if docs:
                    f.write("/**\n")
                    for line in docs.split('\n'):
                        f.write(f" * {line}\n")
                    f.write(" */\n")

                ret_type = "void"
                if len(results) == 1:
                     ret_type = map_type(results[0]['type'], types_list)
                elif len(results) > 1:
                    types = [map_type(r['type'], types_list) for r in results]
                    ret_type = f"std::tuple<{', '.join(types)}>"

                param_strs = []
                for p in params:
                    p_name = to_snake_case(p['name'])
                    p_type = map_type(p['type'], types_list)
                    param_strs.append(f"{p_type} {p_name}")
                
                f.write(f"{ret_type} {to_snake_case(func_name)}({', '.join(param_strs)}) noexcept;\n\n")

            f.write("} // namespace fireball\n")
        
        print(f"Generated: {output_path}")


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <wit_dir> <output_dir>", file=sys.stderr)
        sys.exit(1)
    
    wit_dir = sys.argv[1]
    output_dir = sys.argv[2]
    
    if not os.path.exists(wit_dir):
        print(f"Error: WIT directory not found: {wit_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Parsing WIT package: {wit_dir}")
    wit_json = parse_wit_package(wit_dir)
    
    print(f"Generating C++ headers to {output_dir}...")
    generate_cpp(wit_json, output_dir)
    
    print("Done!")


if __name__ == "__main__":
    main()
