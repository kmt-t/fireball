#!/usr/bin/env python3
import sys
import os
from pathlib import Path

def to_snake_case(name):
    return name.replace("-", "_")

def to_upper_snake_case(name):
    return name.replace("-", "_").upper()

def map_type(wit_type):
    mapping = {
        "u32": "uint32_t",
        "u64": "uint64_t",
        "bool": "bool",
        "shm-id": "shm_id",
        "device-id": "device_id",
        "channel-id": "channel_id",
        "task-id": "task_id",
        "uri-handle": "shm_id",
        "byte-count": "byte_count",
        "operation-result": "operation_result",
        "recovery-strategy": "recovery_strategy",
        "log-level": "log_level",
        "string": "std::string_view",
        "list<u8>": "binary_view",
        "service-id": "service_id",
        "message-handle": "message_handle",
        "address": "address",
    }
    
    if wit_type.startswith("result<"):
        parts = wit_type[7:-1].split(",")
        t_raw = parts[0].strip()
        e_raw = parts[1].strip() if len(parts) > 1 else "recovery-strategy"
        
        t = map_type(t_raw)
        e = map_type(e_raw)
        
        if t == "_": return "operation_result"
        return f"result<{t}, {e}>"

    return mapping.get(wit_type, to_snake_case(wit_type))

class WitToCpp:
    def __init__(self, wit_path):
        self.wit_path = wit_path
        self.interfaces = []

    def parse_contracts(self, doc_buffer):
        contracts = {"pre": [], "post": [], "inv": []}
        general_doc = []
        for d in doc_buffer:
            if d.startswith("@pre:"): contracts["pre"].append(d[5:].strip())
            elif d.startswith("@post:"): contracts["post"].append(d[6:].strip())
            elif d.startswith("@inv:"): contracts["inv"].append(d[5:].strip())
            else: general_doc.append(d)
        return general_doc, contracts

    def parse(self):
        with open(self.wit_path, 'r') as f:
            lines = f.readlines()

        current_iface = None
        current_res = None
        current_enum = None
        current_record = None
        doc_buffer = []

        for line in lines:
            clean_line = line.strip()
            if not clean_line: continue
            
            if clean_line.startswith("///"):
                doc_buffer.append(clean_line[3:].strip())
                continue

            if clean_line.startswith("interface "):
                name = clean_line.split()[1]
                current_iface = {"name": to_snake_case(name), "enums": [], "records": [], "resources": [], "types": []}
                self.interfaces.append(current_iface)
                doc_buffer = []
                continue

            if not current_iface: continue

            if clean_line.startswith("type "):
                parts = clean_line[5:].split("=")
                if len(parts) == 2:
                    tname = parts[0].strip()
                    ttarget = parts[1].split("//")[0].replace(";", "").strip()
                    current_iface["types"].append((to_snake_case(tname), map_type(ttarget)))
                doc_buffer = []
                continue

            if clean_line.startswith("enum "):
                name = clean_line.split()[1]
                current_enum = {"name": to_snake_case(name), "values": [], "doc": doc_buffer}
                current_iface["enums"].append(current_enum)
                doc_buffer = []
                continue

            if clean_line.startswith("record "):
                name = clean_line.split()[1]
                current_record = {"name": to_snake_case(name), "fields": [], "doc": doc_buffer}
                current_iface["records"].append(current_record)
                doc_buffer = []
                continue

            if clean_line.startswith("resource "):
                name = clean_line.split()[1]
                doc, contracts = self.parse_contracts(doc_buffer)
                current_res = {"name": to_snake_case(name), "methods": [], "doc": doc, "contracts": contracts}
                current_iface["resources"].append(current_res)
                doc_buffer = []
                continue

            if clean_line == "}":
                current_res = None
                current_enum = None
                current_record = None
                doc_buffer = []
                continue

            if current_enum:
                val = clean_line.split("//")[0].replace(",", "").strip()
                if val: current_enum["values"].append(to_upper_snake_case(val))
                continue

            if current_record:
                if ":" in clean_line:
                    fname, ftype = clean_line.split("//")[0].replace(",", "").split(":")
                    current_record["fields"].append((to_snake_case(fname.strip()), map_type(ftype.strip())))
                continue

            if current_res and ":" in clean_line and "func(" in clean_line:
                name, rest = clean_line.split(":", 1)
                args_raw = rest[rest.find("(")+1 : rest.find(")")]
                ret_raw = "void"
                if "->" in rest:
                    ret_raw = rest[rest.find("->")+2 : rest.rfind(";")].strip()
                
                args = []
                if args_raw.strip():
                    for arg_pair in args_raw.split(","):
                        if ":" in arg_pair:
                            aname, atype = arg_pair.split(":")
                            args.append((aname.strip(), map_type(atype.strip())))
                
                doc, contracts = self.parse_contracts(doc_buffer)
                current_res["methods"].append({
                    "name": to_snake_case(name.strip()),
                    "args": args,
                    "return": map_type(ret_raw),
                    "doc": doc,
                    "contracts": contracts
                })
                doc_buffer = []

    def generate(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        for iface in self.interfaces:
            path = Path(out_dir) / f"{iface['name']}.hxx"
            with open(path, "w") as f:
                f.write("/**\n * Auto-generated from WIT. Do not edit.\n */\n")
                f.write("#pragma once\n\n#include <fireball_types.hxx>\n")
                
                if iface['name'] != "types":
                    f.write("#include <gen/types.hxx>\n")
                
                f.write("#include <fireball_config.hxx>\n#include <cstdint>\n\n")
                f.write("namespace fireball {\n\n")
                
                for tname, ttarget in iface['types']:
                    f.write(f"using {tname} = {ttarget};\n")
                if iface['types']: f.write("\n")

                for en in iface['enums']:
                    if en['doc']:
                        f.write("/**\n")
                        for d in en['doc']: f.write(f" * {d}\n")
                        f.write(" */\n")
                    f.write(f"enum class {en['name']} : uint8_t {{\n")
                    for v in en['values']: f.write(f"  {v},\n")
                    f.write("};\n\n")

                for rec in iface['records']:
                    if rec['doc']:
                        f.write("/**\n")
                        for d in rec['doc']: f.write(f" * {d}\n")
                        f.write(" */\n")
                    f.write(f"struct {rec['name']} {{\n")
                    for fname, ftype in rec['fields']:
                        f.write(f"  {ftype} {fname};\n")
                    f.write("};\n\n")

                for res in iface['resources']:
                    f.write("/**\n")
                    for d in res['doc']: f.write(f" * {d}\n")
                    for inv in res['contracts']['inv']:
                        f.write(f" * @invariant FB_ASSERT({inv})\n")
                    f.write(" */\n")
                    f.write(f"struct {res['name']}_interface : public component {{\n")
                    for m in res['methods']:
                        f.write("  /**\n")
                        for d in m['doc']: f.write(f"   * {d}\n")
                        for p in m['contracts']['pre']: f.write(f"   * @note Pre-condition: FB_ASSERT({p})\n")
                        for p in m['contracts']['post']: f.write(f"   * @note Post-condition: FB_ASSERT({p})\n")
                        for i in m['contracts']['inv']: f.write(f"   * @note Local-Invariant: FB_ASSERT({i})\n")
                        f.write("   */\n")
                        args_list = [f"{a[1]} {to_snake_case(a[0])}" for a in m['args']]
                        f.write(f"  virtual {m['return']} {m['name']}({', '.join(args_list)}) = 0;\n")
                    f.write("};\n\n")
                f.write("} // namespace fireball\n")
            print(f"Generated: {path}")

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    gen = WitToCpp(sys.argv[1])
    gen.parse()
    gen.generate(sys.argv[2])
