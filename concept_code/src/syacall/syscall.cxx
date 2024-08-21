/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#include <syscall/syscall.hxx>

namespace fireball {
namespace syscall {

uint64_t global_clock() { return 0U; }

handle_t open(const char* name) { return 0; }

void close(handle_t h) {}

uint32_t block_size(handle_t h) { return 0U; }

void set_read_buff(handle_t h, uint8_t* buff, uint32_t bnum) {}

void get_read_buff(handle_t h, uint8_t** buff, uint32_t* bnum) {}

void set_write_buff(handle_t h, uint8_t* buff, uint32_t bnum) {}

void get_write_buff(handle_t h, uint8_t** buff, uint32_t* bnum) {}

bool get_option(handle_t h, uint16_t code, uint8_t* opt, uint32_t opt_size) {
  return false;
}

bool set_option(handle_t h, uint32_t code, const uint8_t* opt, uint32_t optsize) {
  return false;
}

transaction_t execute(handle_t h, uint16_t cmd, uint8_t* param,
                      uint32_t param_size) {
  return 0;
}

transaction_status wait(transaction_t trans) {
  return transaction_status::COMPLETE;
}

transaction_status cancel(transaction_t trans) {
  return transaction_status::COMPLETE;
}

transaction_t read_stream(handle_t h, uint8_t* buff, uint32_t bnum) {
  return 0;
}

transaction_t write_stream(handle_t h, const uint8_t* buff, uint32_t bnum) {
  return 0;
}

transaction_t read_storage(handle_t h, block_address_t addr, uint8_t* buff,
                           uint32_t bnum) {
  return 0;
}

transaction_t write_storage(handle_t h, block_address_t addr,
                            const uint8_t* buff,
                            uint32_t bnum) {
  return 0;
}

}  // namespace syscall
}  // namespace fireball
