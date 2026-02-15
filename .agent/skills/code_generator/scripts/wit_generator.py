#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

# This script needs 'wit-parser'. Install with:
# pip install wit-parser
from wit_parser import parse

def to_snake_case(name):
    """Converts kebab-case or CamelCase to snake_case."""
    return ''.join(['_' + i.lower() if i.isupper() else i for i in name]).lstrip('_')

def to_upper_snake_case(name):
    """Converts a name to UPPER_SNAKE_CASE for header guards."""
    return to_snake_case(name).upper()

def generate_hpp(interface_name, namespace, out_dir):
    """Generates a C++ header file for a given interface."""
    snake_name = to_snake_case(interface_name)
    guard_name = f"FIREBALL_INC_{namespace.upper()}_{snake_name.upper()}_HXX"
    
    header_content = f"""#ifndef {guard_name}
#define {guard_name}

namespace fireball::{namespace} {{

  // @struct {snake_name}
  // @brief Interface for {interface_name}.
  // @note This file is auto-generated from a .wit file. Do not edit.
  struct {snake_name} {{
    virtual ~{snake_name}() = default;

    // TODO: Define pure virtual methods based on WIT spec.
  }};

}} // namespace fireball::{namespace}

#endif // {guard_name}
"""
    
    output_path = out_dir / f"{snake_name}.hxx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(header_content)
    print(f"Generated: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate C++ headers from a WIT file.")
    parser.add_argument("wit_path", type=Path, help="Path to the input .wit file.")
    parser.add_argument("out_dir", type=Path, help="Directory to output the generated .hxx files.")
    
    args = parser.parse_args()

    if not args.wit_path.is_file():
        print(f"Error: WIT file not found at {args.wit_path}")
        return

    # 1. Parse the WIT file (Uniqueness/Validity Check)
    try:
        ast = parse(args.wit_path)
    except Exception as e:
        print(f"Error parsing WIT file: {e}")
        return
        
    # 2. Generate C++ code for each interface
    for interface in ast.interfaces:
        # Assuming the namespace is the second-to-last part of the WIT file's parent directory
        # e.g., docs/components -> components
        namespace = args.wit_path.parent.name
        
        # WIT interface names are kebab-case
        interface_name = interface.name
        
        # Determine the output subdirectory based on the namespace
        output_subdirectory = args.out_dir / namespace
        
        generate_hpp(interface_name, namespace, output_subdirectory)
        
    print("\nDone.")

if __name__ == "__main__":
    main()
