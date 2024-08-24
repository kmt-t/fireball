/**
 * Software SoC for Fireball Project
 *
 * Copyright (c) 2024 Takuya Matsunaga.
 */
#ifndef __COMMONS_HXX__
#define __COMMONS_HXX__

#include <cstdint>

#if HAS_FP16
typedef __fp16 fp16_t;
#else
typedef int16_t fp16_t;
#endif  // #if HAS_FP16

typedef float fp32_t;
typedef double fp64_t;

#include <allocator/stdcxx_allocator.hxx>

#endif  // #ifndef __COMMONS_HXX__
