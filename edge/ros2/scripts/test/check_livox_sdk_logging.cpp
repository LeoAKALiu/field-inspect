#include <cassert>
#include <spdlog/spdlog.h>
#include <spdlog/sinks/null_sink.h>
void InitLogger();
void UninitLogger();
int main() {
  auto host = spdlog::null_logger_mt("host-regression");
  spdlog::set_default_logger(host);
  for (int i = 0; i < 2; ++i) {
    InitLogger();
    UninitLogger();
    assert(spdlog::get("host-regression") == host);
    assert(spdlog::default_logger() == host);
    spdlog::info("host survives SDK shutdown");
  }
}
