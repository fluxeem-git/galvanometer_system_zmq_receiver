#include <zmq.hpp>
#include <zmq_addon.hpp>

#include <nlohmann/json.hpp>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;

constexpr std::string_view kDefaultEndpoint = "tcp://127.0.0.1:5556";
constexpr std::string_view kDefaultTopic = "terminal/rotation/v1";
constexpr std::uint64_t kMaxSafeInteger = 9'007'199'254'740'991ULL;

std::atomic_bool running{true};

struct Options {
  std::string endpoint{kDefaultEndpoint};
  std::string topic{kDefaultTopic};
  std::chrono::milliseconds stale_after{1'000};
};

struct RotationState {
  std::uint64_t frame_no{};
  std::vector<double> axis;
  std::vector<double> omega;
};

void print_usage(std::ostream& output, const char* program) {
  output << "Usage: " << program
         << " [--endpoint tcp://IP:PORT] [--topic TOPIC] [--stale-ms N]\n"
         << "Defaults:\n"
         << "  --endpoint " << kDefaultEndpoint << '\n'
         << "  --topic    " << kDefaultTopic << '\n'
         << "  --stale-ms 1000\n";
}

Options parse_options(int argc, char* argv[]) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string argument{argv[index]};
    if (argument == "--help" || argument == "-h") {
      print_usage(std::cout, argv[0]);
      std::exit(0);
    }
    if (index + 1 >= argc) {
      throw std::invalid_argument("missing value for " + argument);
    }
    const std::string value{argv[++index]};
    if (argument == "--endpoint") {
      options.endpoint = value;
    } else if (argument == "--topic") {
      options.topic = value;
    } else if (argument == "--stale-ms") {
      const auto milliseconds = std::stoul(value);
      if (milliseconds == 0) {
        throw std::invalid_argument("--stale-ms must be positive");
      }
      options.stale_after = std::chrono::milliseconds{milliseconds};
    } else {
      throw std::invalid_argument("unknown option: " + argument);
    }
  }
  if (options.endpoint.rfind("tcp://", 0) != 0) {
    throw std::invalid_argument("--endpoint must start with tcp://");
  }
  if (options.topic.empty()) {
    throw std::invalid_argument("--topic must not be empty");
  }
  return options;
}

bool read_vector3(const Json& value, std::vector<double>& result) {
  if (!value.is_array() || value.size() != 3) {
    return false;
  }
  result.clear();
  result.reserve(3);
  for (const auto& component : value) {
    if (!component.is_number() || component.is_boolean()) {
      return false;
    }
    const double number = component.get<double>();
    if (!std::isfinite(number)) {
      return false;
    }
    result.push_back(number);
  }
  return true;
}

bool decode_rotation_state(std::string_view text, RotationState& state,
                           std::string& error) {
  try {
    const Json payload = Json::parse(text);
    if (!payload.is_object() || payload.size() != 4 ||
        !payload.contains("axisWorld") ||
        !payload.contains("coordinateFrame") ||
        !payload.contains("mocapFrameNo") ||
        !payload.contains("omegaWorldRadPerSecond")) {
      error = "payload must contain exactly the four RotationStateV1 fields";
      return false;
    }
    if (!payload.at("coordinateFrame").is_string() ||
        payload.at("coordinateFrame").get<std::string>() != "mocap_world") {
      error = "coordinateFrame must equal mocap_world";
      return false;
    }
    const auto& frame = payload.at("mocapFrameNo");
    if (!(frame.is_number_unsigned() || frame.is_number_integer())) {
      error = "mocapFrameNo must be a non-negative integer";
      return false;
    }
    std::uint64_t frame_no{};
    if (frame.is_number_unsigned()) {
      frame_no = frame.get<std::uint64_t>();
    } else {
      const auto signed_frame_no = frame.get<std::int64_t>();
      if (signed_frame_no < 0) {
        error = "mocapFrameNo must be non-negative";
        return false;
      }
      frame_no = static_cast<std::uint64_t>(signed_frame_no);
    }
    if (frame_no > kMaxSafeInteger) {
      error = "mocapFrameNo exceeds the JSON safe-integer range";
      return false;
    }
    RotationState candidate;
    candidate.frame_no = frame_no;
    if (!read_vector3(payload.at("axisWorld"), candidate.axis) ||
        !read_vector3(payload.at("omegaWorldRadPerSecond"), candidate.omega)) {
      error = "axisWorld and omegaWorldRadPerSecond must be finite vector3 arrays";
      return false;
    }
    state = std::move(candidate);
    return true;
  } catch (const std::exception& exception) {
    error = exception.what();
    return false;
  }
}

std::string_view message_view(const zmq::message_t& message) {
  return {static_cast<const char*>(message.data()), message.size()};
}

void request_stop(int) { running.store(false); }

int receive(const Options& options) {
  zmq::context_t context{1};
  zmq::socket_t subscriber{context, zmq::socket_type::sub};
  subscriber.set(zmq::sockopt::rcvhwm, 10'000);
  subscriber.set(zmq::sockopt::linger, 0);
  subscriber.set(zmq::sockopt::subscribe, options.topic);
  subscriber.connect(options.endpoint);

  std::cout << "Connecting SUB to " << options.endpoint << "\nTopic: "
            << options.topic << "\nPress Ctrl+C to stop.\n";

  auto last_valid = Clock::now();
  bool received_valid = false;
  bool stale_reported = false;
  std::uint64_t received = 0;
  std::uint64_t invalid = 0;

  while (running.load()) {
    zmq::pollitem_t items[] = {{subscriber.handle(), 0, ZMQ_POLLIN, 0}};
    try {
      zmq::poll(items, 1, std::chrono::milliseconds{200});
    } catch (const zmq::error_t& error) {
      // POSIX signals interrupt poll with EINTR. Ctrl+C is a normal shutdown,
      // not a receiver failure; Windows normally returns from poll directly.
      if (error.num() == EINTR && !running.load()) {
        break;
      }
      throw;
    }
    if ((items[0].revents & ZMQ_POLLIN) == 0) {
      if (Clock::now() - last_valid > options.stale_after && !stale_reported) {
        std::cerr << (received_valid ? "STALE" : "WAITING")
                  << ": no valid rotation payload within watchdog interval\n";
        stale_reported = true;
      }
      continue;
    }

    std::vector<zmq::message_t> frames;
    const auto frame_count = zmq::recv_multipart(subscriber, std::back_inserter(frames));
    ++received;
    if (!frame_count || frames.size() != 2 ||
        message_view(frames[0]) != options.topic) {
      ++invalid;
      std::cerr << "INVALID: expected exactly [exact topic, JSON payload]\n";
      continue;
    }

    RotationState state;
    std::string error;
    if (!decode_rotation_state(message_view(frames[1]), state, error)) {
      ++invalid;
      std::cerr << "INVALID: " << error << '\n';
      continue;
    }

    last_valid = Clock::now();
    received_valid = true;
    stale_reported = false;
    const double speed_rad_s = std::sqrt(
        state.omega[0] * state.omega[0] + state.omega[1] * state.omega[1] +
        state.omega[2] * state.omega[2]);
    constexpr double pi = 3.14159265358979323846;
    const double speed_rpm = speed_rad_s * 60.0 / (2.0 * pi);
    std::cout << "frame=" << state.frame_no << " speed_rad_s=" << speed_rad_s
              << " speed_rpm=" << speed_rpm << " omega=[" << state.omega[0]
              << ',' << state.omega[1] << ',' << state.omega[2] << "] axis=["
              << state.axis[0] << ',' << state.axis[1] << ',' << state.axis[2]
              << "]\n";
  }

  subscriber.close();
  context.close();
  std::cout << "Stopped. received=" << received << " invalid=" << invalid << '\n';
  return 0;
}

}  // namespace

int main(int argc, char* argv[]) {
  try {
    const Options options = parse_options(argc, argv);
    std::signal(SIGINT, request_stop);
    std::signal(SIGTERM, request_stop);
    return receive(options);
  } catch (const std::exception& exception) {
    std::cerr << "Error: " << exception.what() << "\n\n";
    print_usage(std::cerr, argv[0]);
    return 2;
  }
}
