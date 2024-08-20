/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __SYSCALL_HXX__
#define __SYSCALL_HXX__

#include <commons.hxx>

namespace fireball {
namespace syscall {

typedef intptr_t handle_t;
typedef int16_t transaction_t;
typedef uint32_t block_address_t;

static constexpr handle_t INVALID_HANDLE = 0;
static constexpr uint16_t INVALID_TRANSACTION = 0xFFFF;

typedef enum class transaction_status : int8_t {
  COMPLETE = 0,
  QUEUING = 1,
  EXECUTING = 2,
  CANCELED = 3,
  DEVICE_ERROR = -1,
  DEVICE_TIMEOUT = -2,
  SYSTEM_BUSSY = -3,
  TRANSAACTION_QUEUE_IS_FULL = -4,
} transaction_status_t;

extern uint64_t global_clock();

extern handle_t open(const char* name);

extern void close(handle_t h);

extern uint32_t block_size(handle_t h);

extern bool setopt(handle_t h, uint32_t code, const uint8_t* opt,
                   uint32_t optsize);

extern bool getopt(handle_t h, uint16_t code, uint8_t* opt, uint32_t opt_size);

extern transaction_t execute(handle_t h, uint16_t cmd, uint8_t* param,
                             uint32_t param_size);

extern transaction_status wait(transaction_t trans);

extern transaction_status cancel(transaction_t trans);

extern transaction_t read_strem(uint8_t* buff, uint32_t bnum);

extern transaction_t write_strem(const uint8_t* buff, uint32_t bnum);

extern transaction_t read_storagek(block_address_t addr, uint8_t* buff,
                                   uint32_t bnum);

extern transaction_t write_storage(block_address_t addr, const uint8_t* buff,
                                   uint32_t bnum);

}  // namespace syscall
}  // namespace fireball

#endif  // #ifndef __SYSCALL_HXX__
