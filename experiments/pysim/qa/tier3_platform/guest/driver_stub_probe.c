// QA-only command adapter imports; not the production guest HAL WIT adapter.
#include <stdint.h>

__attribute__((import_module("fireball-qa"), import_name("command"))) extern uint64_t
qa_command(uint32_t command, uint32_t key0, uint32_t value0, uint32_t key1, uint32_t value1,
           uint32_t key2, uint32_t value2, uint32_t count);

__attribute__((import_module("fireball-qa"), import_name("stream"))) extern uint32_t
qa_stream(uint32_t command, uint32_t slot, uint32_t offset, uint32_t length);

uint64_t command_probe(uint32_t command, uint32_t key0, uint32_t value0, uint32_t key1,
                       uint32_t value1, uint32_t key2, uint32_t value2, uint32_t count) {
  return qa_command(command, key0, value0, key1, value1, key2, value2, count);
}

uint32_t stream_probe(uint32_t command, uint32_t slot, uint32_t offset, uint32_t length) {
  return qa_stream(command, slot, offset, length);
}
