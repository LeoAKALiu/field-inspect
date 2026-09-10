// Copyright 2026 SCOUT Mini Team
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "hik_camera_ros2/camera_backend.hpp"

#ifdef USE_HIK_MVS
#include "MvCameraControl.h"
#endif

#ifdef USE_ARAVIS
#include <arv.h>
#endif

#include <cmath>
#include <chrono>
#include <cstring>
#include <memory>
#include <sstream>
#include <utility>

namespace hik_camera_ros2
{
namespace
{

class StubBackend final : public CameraBackend
{
public:
  bool open(const CameraConfig & config) override
  {
    requested_backend_ = config.backend;
    stats_.state = "sdk_unavailable";
    stats_.connected = false;
    stats_.last_error = "backend '" + requested_backend_ + "' is unavailable at build time";
    return false;
  }

  bool grab(FrameData &) override
  {
    ++stats_.frames_dropped;
    return false;
  }

  void close() override
  {
    stats_.connected = false;
    stats_.state = "closed";
  }

  CameraStats stats() const override
  {
    return stats_;
  }

  std::string name() const override
  {
    return requested_backend_.empty() ? "stub" : requested_backend_;
  }

private:
  std::string requested_backend_;
  mutable CameraStats stats_;
};

#ifdef USE_HIK_MVS
class MvsBackend final : public CameraBackend
{
public:
  bool open(const CameraConfig & config) override
  {
    config_ = config;
    close();
    stats_.device_model.clear();
    stats_.device_serial.clear();
    stats_.sensor_width = 0;
    stats_.sensor_height = 0;
    MV_CC_DEVICE_INFO_LIST devices{};
    if (MV_CC_EnumDevices(MV_GIGE_DEVICE, &devices) != MV_OK) {
      stats_.last_error = "MVS enum devices failed";
      stats_.state = "error";
      return false;
    }

    MV_CC_DEVICE_INFO * selected = nullptr;
    for (unsigned int index = 0; index < devices.nDeviceNum; ++index) {
      auto * info = devices.pDeviceInfo[index];
      if (info == nullptr || info->nTLayerType != MV_GIGE_DEVICE) {
        continue;
      }
      const auto ip = info->SpecialInfo.stGigEInfo.nCurrentIp;
      std::ostringstream stream;
      stream << ((ip >> 24) & 0xFF) << "." << ((ip >> 16) & 0xFF) << "."
             << ((ip >> 8) & 0xFF) << "." << (ip & 0xFF);
      if (stream.str() == config.camera_ip) {
        selected = info;
        break;
      }
    }

    if (selected == nullptr) {
      stats_.last_error = "MVS camera not found: " + config.camera_ip;
      stats_.state = "disconnected";
      return false;
    }

    const auto & device = selected->SpecialInfo.stGigEInfo;
    stats_.device_model = std::string(
      reinterpret_cast<const char *>(device.chModelName),
      strnlen(reinterpret_cast<const char *>(device.chModelName), sizeof(device.chModelName)));
    stats_.device_serial = std::string(
      reinterpret_cast<const char *>(device.chSerialNumber),
      strnlen(
        reinterpret_cast<const char *>(device.chSerialNumber),
        sizeof(device.chSerialNumber)));
    if (!config.expected_model.empty() && stats_.device_model != config.expected_model) {
      stats_.last_error = "MVS camera model mismatch: expected " + config.expected_model +
        ", actual " + stats_.device_model;
      stats_.state = "identity_mismatch";
      return false;
    }
    if (!config.expected_serial.empty() && stats_.device_serial != config.expected_serial) {
      stats_.last_error = "MVS camera serial mismatch: expected " + config.expected_serial +
        ", actual " + stats_.device_serial;
      stats_.state = "identity_mismatch";
      return false;
    }

    int rc = MV_CC_CreateHandle(&handle_, selected);
    if (rc != MV_OK) {
      return fail("create handle", rc);
    }
    rc = MV_CC_OpenDevice(handle_);
    if (rc != MV_OK) {
      return fail("open device", rc);
    }
    rc = MV_CC_SetEnumValue(handle_, "PixelFormat", PixelType_Gvsp_BGR8_Packed);
    if (rc != MV_OK) {
      return fail("set PixelFormat=BGR8", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "OffsetX", 0);
    if (rc != MV_OK) {
      return fail("reset OffsetX", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "OffsetY", 0);
    if (rc != MV_OK) {
      return fail("reset OffsetY", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "Width", config.width);
    if (rc != MV_OK) {
      return fail("set Width", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "Height", config.height);
    if (rc != MV_OK) {
      return fail("set Height", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "OffsetX", config.offset_x);
    if (rc != MV_OK) {
      return fail("set OffsetX", rc);
    }
    rc = MV_CC_SetIntValueEx(handle_, "OffsetY", config.offset_y);
    if (rc != MV_OK) {
      return fail("set OffsetY", rc);
    }
    rc = MV_CC_SetBoolValue(handle_, "AcquisitionFrameRateEnable", true);
    if (rc != MV_OK) {
      return fail("enable AcquisitionFrameRate", rc);
    }
    rc = MV_CC_SetFloatValue(
      handle_, "AcquisitionFrameRate", static_cast<float>(config.frame_rate));
    if (rc != MV_OK) {
      return fail("set AcquisitionFrameRate", rc);
    }
    const unsigned int exposure_auto_mode = config.exposure_auto ? 2U : 0U;
    rc = MV_CC_SetEnumValue(handle_, "ExposureAuto", exposure_auto_mode);
    if (rc != MV_OK) {
      return fail("set ExposureAuto", rc);
    }
    if (!config.exposure_auto) {
      rc = MV_CC_SetFloatValue(
        handle_, "ExposureTime", static_cast<float>(config.exposure_time_us));
      if (rc != MV_OK) {
        return fail("set ExposureTime", rc);
      }
    }
    const unsigned int gain_auto_mode = config.gain_auto ? 2U : 0U;
    rc = MV_CC_SetEnumValue(handle_, "GainAuto", gain_auto_mode);
    if (rc != MV_OK) {
      return fail("set GainAuto", rc);
    }
    if (!config.gain_auto) {
      rc = MV_CC_SetFloatValue(handle_, "Gain", static_cast<float>(config.gain_db));
      if (rc != MV_OK) {
        return fail("set Gain", rc);
      }
    }
    if ((config.sensor_width > 0 && !verify_integer("SensorWidth", config.sensor_width)) ||
      (config.sensor_height > 0 && !verify_integer("SensorHeight", config.sensor_height)) ||
      !verify_enum("PixelFormat", PixelType_Gvsp_BGR8_Packed) ||
      !verify_integer("Width", config.width) || !verify_integer("Height", config.height) ||
      !verify_integer("OffsetX", config.offset_x) ||
      !verify_integer("OffsetY", config.offset_y) ||
      !verify_float("AcquisitionFrameRate", config.frame_rate) ||
      !verify_enum("ExposureAuto", exposure_auto_mode) ||
      (!config.exposure_auto && !verify_float("ExposureTime", config.exposure_time_us)) ||
      !verify_enum("GainAuto", gain_auto_mode) ||
      (!config.gain_auto && !verify_float("Gain", config.gain_db)))
    {
      return false;
    }
    stats_.sensor_width = config.sensor_width;
    stats_.sensor_height = config.sensor_height;
    rc = MV_CC_StartGrabbing(handle_);
    if (rc != MV_OK) {
      return fail("start grabbing", rc);
    }
    if (opened_once_) {
      ++stats_.reconnect_count;
    }
    opened_once_ = true;
    stats_.connected = true;
    stats_.state = "connected";
    stats_.last_error.clear();
    return true;
  }

  bool grab(FrameData & frame) override
  {
    if (handle_ == nullptr) {
      ++stats_.frames_dropped;
      return false;
    }

    MV_FRAME_OUT raw{};
    const int rc = MV_CC_GetImageBuffer(handle_, &raw, 1000);
    if (rc != MV_OK) {
      ++stats_.frames_dropped;
      stats_.connected = false;
      stats_.state = "disconnected";
      stats_.last_error = "MVS grab timeout";
      return false;
    }

    frame.width = raw.stFrameInfo.nWidth;
    frame.height = raw.stFrameInfo.nHeight;
    frame.encoding = "bgr8";
    frame.bytes.resize(raw.stFrameInfo.nFrameLen);
    std::memcpy(frame.bytes.data(), raw.pBufAddr, raw.stFrameInfo.nFrameLen);
    MV_CC_FreeImageBuffer(handle_, &raw);

    ++stats_.frames_published;
    stats_.connected = true;
    stats_.state = "streaming";
    return true;
  }

  void close() override
  {
    if (handle_ != nullptr) {
      MV_CC_StopGrabbing(handle_);
      MV_CC_CloseDevice(handle_);
      MV_CC_DestroyHandle(handle_);
      handle_ = nullptr;
    }
    stats_.connected = false;
    stats_.state = "closed";
  }

  CameraStats stats() const override
  {
    return stats_;
  }

  std::string name() const override
  {
    return "mvs";
  }

private:
  bool fail(const std::string & operation, int rc)
  {
    close();
    std::ostringstream stream;
    stream << "MVS " << operation << " failed (0x" << std::hex << rc << ")";
    stats_.last_error = stream.str();
    stats_.state = "error";
    return false;
  }

  bool verify_integer(const char * key, int expected)
  {
    MVCC_INTVALUE_EX value{};
    const int rc = MV_CC_GetIntValueEx(handle_, key, &value);
    if (rc != MV_OK) {
      return fail(std::string("read back ") + key, rc);
    }
    if (value.nCurValue != expected) {
      close();
      stats_.last_error = std::string("MVS ") + key + " readback mismatch: requested " +
        std::to_string(expected) + ", actual " + std::to_string(value.nCurValue);
      stats_.state = "error";
      return false;
    }
    return true;
  }

  bool verify_enum(const char * key, unsigned int expected)
  {
    MVCC_ENUMVALUE value{};
    const int rc = MV_CC_GetEnumValue(handle_, key, &value);
    if (rc != MV_OK) {
      return fail(std::string("read back ") + key, rc);
    }
    if (value.nCurValue != expected) {
      close();
      stats_.last_error = std::string("MVS ") + key + " readback mismatch";
      stats_.state = "error";
      return false;
    }
    return true;
  }

  bool verify_float(const char * key, double expected)
  {
    MVCC_FLOATVALUE value{};
    const int rc = MV_CC_GetFloatValue(handle_, key, &value);
    if (rc != MV_OK) {
      return fail(std::string("read back ") + key, rc);
    }
    if (std::fabs(static_cast<double>(value.fCurValue) - expected) > 0.01) {
      close();
      stats_.last_error = std::string("MVS ") + key + " readback mismatch: requested " +
        std::to_string(expected) + ", actual " + std::to_string(value.fCurValue);
      stats_.state = "error";
      return false;
    }
    return true;
  }

  CameraConfig config_;
  void * handle_{nullptr};
  bool opened_once_{false};
  mutable CameraStats stats_;
};
#endif

#ifdef USE_ARAVIS
class AravisBackend final : public CameraBackend
{
public:
  bool open(const CameraConfig & config) override
  {
    config_ = config;
    close();
    GError * error = nullptr;
    camera_ = arv_camera_new(config.camera_ip.c_str(), &error);
    if (error != nullptr) {
      stats_.last_error = error->message;
      g_error_free(error);
      stats_.state = "error";
      return false;
    }

    stream_ = arv_camera_create_stream(camera_, nullptr, nullptr, &error);
    if (error != nullptr) {
      stats_.last_error = error->message;
      g_error_free(error);
      close();
      return false;
    }

    const int payload = arv_camera_get_payload(camera_, &error);
    if (error != nullptr) {
      stats_.last_error = error->message;
      g_error_free(error);
      close();
      return false;
    }

    for (int index = 0; index < 10; ++index) {
      arv_stream_push_buffer(stream_, arv_buffer_new(payload, nullptr));
    }

    arv_camera_start_acquisition(camera_, &error);
    if (error != nullptr) {
      stats_.last_error = error->message;
      g_error_free(error);
      close();
      return false;
    }

    stats_.connected = true;
    stats_.state = "connected";
    stats_.last_error.clear();
    return true;
  }

  bool grab(FrameData & frame) override
  {
    if (stream_ == nullptr) {
      ++stats_.frames_dropped;
      return false;
    }

    GError * error = nullptr;
    ArvBuffer * buffer = arv_stream_timeout_pop_buffer(stream_, 1000000, &error);
    if (buffer == nullptr || error != nullptr) {
      if (error != nullptr) {
        stats_.last_error = error->message;
        g_error_free(error);
      }
      ++stats_.frames_dropped;
      stats_.connected = false;
      stats_.state = "disconnected";
      return false;
    }

    if (arv_buffer_get_status(buffer) == ARV_BUFFER_STATUS_SUCCESS) {
      size_t size = 0;
      const void * data = arv_buffer_get_data(buffer, &size);
      frame.width = config_.width;
      frame.height = config_.height;
      frame.encoding = "mono8";
      frame.bytes.assign(
        static_cast<const std::uint8_t *>(data),
        static_cast<const std::uint8_t *>(data) + size);
      ++stats_.frames_published;
      stats_.connected = true;
      stats_.state = "streaming";
      arv_stream_push_buffer(stream_, buffer);
      return true;
    }

    ++stats_.frames_dropped;
    arv_stream_push_buffer(stream_, buffer);
    return false;
  }

  void close() override
  {
    if (camera_ != nullptr) {
      GError * error = nullptr;
      arv_camera_stop_acquisition(camera_, &error);
      if (error != nullptr) {
        g_error_free(error);
      }
      g_object_unref(camera_);
      camera_ = nullptr;
    }
    if (stream_ != nullptr) {
      g_object_unref(stream_);
      stream_ = nullptr;
    }
    stats_.connected = false;
    stats_.state = "closed";
  }

  CameraStats stats() const override
  {
    return stats_;
  }

  std::string name() const override
  {
    return "aravis";
  }

private:
  CameraConfig config_;
  ArvCamera * camera_{nullptr};
  ArvStream * stream_{nullptr};
  mutable CameraStats stats_;
};
#endif

}  // namespace

std::unique_ptr<CameraBackend> create_camera_backend(const std::string & backend_name)
{
  if (backend_name == "mvs") {
#ifdef USE_HIK_MVS
    return std::make_unique<MvsBackend>();
#else
    auto backend = std::make_unique<StubBackend>();
    CameraConfig config;
    config.backend = backend_name;
    backend->open(config);
    return backend;
#endif
  }

  if (backend_name == "aravis") {
#ifdef USE_ARAVIS
    return std::make_unique<AravisBackend>();
#else
    auto backend = std::make_unique<StubBackend>();
    CameraConfig config;
    config.backend = backend_name;
    backend->open(config);
    return backend;
#endif
  }

  auto backend = std::make_unique<StubBackend>();
  CameraConfig config;
  config.backend = backend_name;
  backend->open(config);
  return backend;
}

}  // namespace hik_camera_ros2
