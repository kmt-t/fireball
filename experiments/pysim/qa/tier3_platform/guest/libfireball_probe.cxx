#include "libfireball.hxx"

extern "C" __attribute__((export_name("probe"))) fireball::u32 probe(fireball::u32 output) {
  auto* results = reinterpret_cast<fireball::u32*>(output);
  results[0] = fireball::fireball_call0(16u);
  results[1] = fireball::fireball_call1(17u, 0x80000000u);
  results[2] = fireball::fireball_call2(18u, 0x80000000u, 0xffffffffu);
  results[3] = fireball::fireball_call3(19u, 0x80000000u, 2u, 0xffffffffu);
  results[4] = fireball::fireball_call4(20u, 0x80000000u, 2u, 3u, 0xffffffffu);
  results[5] = fireball::fireball_call5(21u, 0x80000000u, 2u, 3u, 4u, 0xffffffffu);
  results[6] = fireball::fireball_call6(22u, 0x80000000u, 2u, 3u, 4u, 5u, 0xffffffffu);
  results[7] = fireball::fireball_virq_register(0x80000000u, 0xffffffffu);
  results[8] = fireball::fireball_virq_unregister(0xffffffffu);
  results[9] = fireball::fireball_vdma_start(0x80000000u, 0xffffffffu, 7u);
  return 10;
}
