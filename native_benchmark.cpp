#include <sqlite3.h>
#include <dlfcn.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <stdexcept>
#include <string>
#include <vector>

class SqliteApi {
    void* library;
    void* symbol(const char* name) {
        void* address = dlsym(library, name);
        if (!address) throw std::runtime_error(std::string("Missing SQLite symbol: ") + name);
        return address;
    }
public:
    explicit SqliteApi(const char* path) : library(dlopen(path, RTLD_NOW | RTLD_LOCAL)) {
        if (!library) throw std::runtime_error("Cannot load Python's SQLite library");
    }
    ~SqliteApi() { if (library) dlclose(library); }
#define LOAD_SQLITE_FUNCTION(name) decltype(&::name) name = reinterpret_cast<decltype(&::name)>(symbol(#name));
    LOAD_SQLITE_FUNCTION(sqlite3_open_v2)
    LOAD_SQLITE_FUNCTION(sqlite3_close_v2)
    LOAD_SQLITE_FUNCTION(sqlite3_exec)
    LOAD_SQLITE_FUNCTION(sqlite3_prepare_v2)
    LOAD_SQLITE_FUNCTION(sqlite3_bind_text)
    LOAD_SQLITE_FUNCTION(sqlite3_bind_int64)
    LOAD_SQLITE_FUNCTION(sqlite3_step)
    LOAD_SQLITE_FUNCTION(sqlite3_reset)
    LOAD_SQLITE_FUNCTION(sqlite3_finalize)
    LOAD_SQLITE_FUNCTION(sqlite3_errmsg)
    LOAD_SQLITE_FUNCTION(sqlite3_libversion)
    LOAD_SQLITE_FUNCTION(sqlite3_column_int)
#undef LOAD_SQLITE_FUNCTION
};

using Clock = std::chrono::steady_clock;

double elapsed(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}

struct Measurements {
    long long events = 0;
    long long commits = 0;
    double wall = 0;
    double cpu = 0;
    double generation = 0;
    double inserts = 0;
    double commit = 0;
    double p99_ms = 0;
};

extern "C" int run_native(const char* library, const char* database_path, const char* setup,
                          double seconds, double warmup_seconds, int batch_size, int statement_rows,
                          char* output, size_t capacity) {
    try {
        SqliteApi api(library);
        sqlite3* database = nullptr;
        sqlite3_stmt* statement = nullptr;
        auto check = [&](int code) {
            if (code != SQLITE_OK) throw std::runtime_error(api.sqlite3_errmsg(database));
        };
        auto execute = [&](const char* sql) {
            check(api.sqlite3_exec(database, sql, nullptr, nullptr, nullptr));
        };
        try {
            check(api.sqlite3_open_v2(database_path, &database,
                  SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nullptr));
            execute(setup);
            auto scalar = [&](const char* sql) {
                sqlite3_stmt* query = nullptr;
                check(api.sqlite3_prepare_v2(database, sql, -1, &query, nullptr));
                if (api.sqlite3_step(query) != SQLITE_ROW) {
                    api.sqlite3_finalize(query);
                    throw std::runtime_error("Cannot read back SQLite setting");
                }
                int value = api.sqlite3_column_int(query, 0);
                check(api.sqlite3_finalize(query));
                return value;
            };
            int synchronous = scalar("PRAGMA synchronous");
            int fullfsync = scalar("PRAGMA fullfsync");
            int checkpoint_fullfsync = scalar("PRAGMA checkpoint_fullfsync");
            int autocheckpoint = scalar("PRAGMA wal_autocheckpoint");
            int journal_is_wal = scalar("SELECT journal_mode = 'wal' FROM pragma_journal_mode");
            std::string insert = "INSERT INTO payment_events VALUES ";
            for (int position = 0; position < statement_rows; ++position) {
                if (position) insert += ",";
                insert += "(?,?,?)";
            }
            check(api.sqlite3_prepare_v2(database, insert.c_str(), -1, &statement, nullptr));
            auto measure = [&](double duration, long long first_event) {
                Measurements result;
                std::vector<std::array<char, 32>> identifiers(batch_size);
                std::vector<long long> amounts(batch_size);
                std::vector<double> latencies;
                auto started = Clock::now();
                auto cpu_started = std::clock();
                do {
                    auto batch_started = Clock::now();
                    for (int offset = 0; offset < batch_size; ++offset) {
                        long long number = first_event + result.events + offset;
                        std::snprintf(identifiers[offset].data(), identifiers[offset].size(), "evt_%012lld", number);
                        amounts[offset] = 100 + number % 100000;
                    }
                    result.generation += elapsed(batch_started);
                    auto inserts_started = Clock::now();
                    execute("BEGIN");
                    for (int offset = 0; offset < batch_size; offset += statement_rows) {
                        for (int row = 0; row < statement_rows; ++row) {
                            check(api.sqlite3_bind_text(statement, row * 3 + 1, identifiers[offset + row].data(), -1, SQLITE_TRANSIENT));
                            check(api.sqlite3_bind_int64(statement, row * 3 + 2, amounts[offset + row]));
                            check(api.sqlite3_bind_text(statement, row * 3 + 3, "captured", 8, SQLITE_STATIC));
                        }
                        if (api.sqlite3_step(statement) != SQLITE_DONE)
                            throw std::runtime_error(api.sqlite3_errmsg(database));
                        check(api.sqlite3_reset(statement));
                    }
                    result.inserts += elapsed(inserts_started);
                    auto commit_started = Clock::now();
                    execute("COMMIT");
                    result.commit += elapsed(commit_started);
                    latencies.push_back(elapsed(batch_started) * 1000);
                    result.events += batch_size;
                    result.commits += 1;
                } while (elapsed(started) < duration);
                result.wall = elapsed(started);
                result.cpu = double(std::clock() - cpu_started) / CLOCKS_PER_SEC;
                std::sort(latencies.begin(), latencies.end());
                result.p99_ms = latencies[static_cast<size_t>(std::ceil(latencies.size() * 0.99)) - 1];
                return result;
            };
            auto warmup = measure(warmup_seconds, 0);
            execute("PRAGMA wal_checkpoint(TRUNCATE)");
            auto result = measure(seconds, warmup.events);
            auto checkpoint_started = Clock::now();
            execute("PRAGMA wal_checkpoint(TRUNCATE)");
            double checkpoint_seconds = elapsed(checkpoint_started);
            check(api.sqlite3_finalize(statement));
            statement = nullptr;
            check(api.sqlite3_close_v2(database));
            database = nullptr;
            int written = std::snprintf(output, capacity,
                "{\"sqlite\":\"%s\",\"events\":%lld,\"commits\":%lld,\"warmup_events\":%lld,"
                "\"elapsed_seconds\":%.9f,\"process_cpu_seconds\":%.9f,\"generation_seconds\":%.9f,"
                "\"insert_seconds\":%.9f,\"commit_seconds\":%.9f,\"batch_p99_ms\":%.9f,"
                "\"final_checkpoint_seconds\":%.9f,\"pragmas\":{\"synchronous\":%d,\"fullfsync\":%d,"
                "\"checkpoint_fullfsync\":%d,\"wal_autocheckpoint\":%d,\"journal_is_wal\":%d}}",
                api.sqlite3_libversion(), result.events, result.commits, warmup.events,
                result.wall, result.cpu, result.generation, result.inserts, result.commit,
                result.p99_ms, checkpoint_seconds, synchronous, fullfsync,
                checkpoint_fullfsync, autocheckpoint, journal_is_wal);
            if (written < 0 || static_cast<size_t>(written) >= capacity)
                throw std::runtime_error("Output buffer too small");
        } catch (...) {
            if (statement) api.sqlite3_finalize(statement);
            if (database) api.sqlite3_close_v2(database);
            throw;
        }
        return 0;
    } catch (const std::exception& error) {
        std::snprintf(output, capacity, "%s", error.what());
        return 1;
    }
}
