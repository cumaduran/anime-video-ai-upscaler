#include "filter_realcugan.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <filesystem>

#include <spdlog/spdlog.h>

#include "conversions.h"
#include "fsutils.h"
#include "logger_manager.h"

namespace video2x {
namespace processors {

FilterRealcugan::FilterRealcugan(
    int gpuid,
    bool tta_mode,
    int scaling_factor,
    int noise_level,
    int num_threads,
    int syncgap,
    const fsutils::StringType model_name
)
    : realcugan_(nullptr),
      gpuid_(gpuid),
      tta_mode_(tta_mode),
      scaling_factor_(scaling_factor),
      noise_level_(noise_level),
      num_threads_(num_threads),
      syncgap_(syncgap),
      model_name_(std::move(model_name)) {}

FilterRealcugan::~FilterRealcugan() {
    delete realcugan_;
    realcugan_ = nullptr;
}

int FilterRealcugan::init(AVCodecContext* dec_ctx, AVCodecContext* enc_ctx, AVBufferRef*) {
    // Construct the model paths using std::filesystem
    std::filesystem::path model_param_path;
    std::filesystem::path model_bin_path;

    fsutils::StringType model_base_name =
        STR("up") + fsutils::to_string_type(scaling_factor_) + STR("x-");

    switch (noise_level_) {
        case -1:
            model_base_name += STR("conservative");
            break;
        case 0:
            model_base_name += STR("no-denoise");
            break;
        default:
            model_base_name += STR("denoise") + fsutils::to_string_type(noise_level_) + STR("x");
            break;
    }

    fsutils::StringType param_file_name = model_base_name + STR(".param");
    fsutils::StringType bin_file_name = model_base_name + STR(".bin");

    // Find the model paths by model name if provided
    model_param_path =
        std::filesystem::path(STR("models")) / STR("realcugan") / model_name_ / param_file_name;
    model_bin_path =
        std::filesystem::path(STR("models")) / STR("realcugan") / model_name_ / bin_file_name;

    // Get the full paths using a function that possibly modifies or validates the path
    std::optional<std::filesystem::path> model_param_full_path =
        fsutils::find_resource(model_param_path);
    std::optional<std::filesystem::path> model_bin_full_path =
        fsutils::find_resource(model_bin_path);

    // Check if the model files exist
    if (!model_param_full_path.has_value()) {
        logger()->error("Real-CUGAN model param file not found: {}", model_param_path.u8string());
        return -1;
    }
    if (!model_bin_full_path.has_value()) {
        logger()->error("Real-CUGAN model bin file not found: {}", model_bin_path.u8string());
        return -1;
    }

    // Create a new Real-CUGAN instance
    realcugan_ = new RealCUGAN(gpuid_, tta_mode_, num_threads_);

    // Store the time bases
    in_time_base_ = dec_ctx->time_base;
    out_time_base_ = enc_ctx->time_base;
    out_pix_fmt_ = enc_ctx->pix_fmt;

    // Load the model
    if (realcugan_->load(model_param_full_path.value(), model_bin_full_path.value()) != 0) {
        logger()->error("Failed to load Real-CUGAN model");
        return -1;
    }

    // Set syncgap to 0 for models-nose
    if (model_name_.find(STR("models-nose")) != fsutils::StringType::npos) {
        syncgap_ = 0;
    }
    // Very-rough synchronization samples small tiles. Pro models can become
    // numerically unstable on those samples, so use the rough full-tile
    // synchronization instead for both 8-bit and high-precision processing.
    if (model_name_ == STR("models-pro") && syncgap_ == 3) {
        logger()->info("Real-CUGAN Pro uses sync gap 2 instead of 3 for stability");
        syncgap_ = 2;
    }

    // The underlying implementation reads this field when processing tiled
    // images. Leaving it uninitialized ignores the user's selected mode.
    realcugan_->syncgap = syncgap_;
    realcugan_->pro_mode = model_name_ == STR("models-pro");

    // Set realcugan parameters
    realcugan_->scale = scaling_factor_;
    realcugan_->noise = noise_level_;
    realcugan_->prepadding = 10;

    // Set prepadding based on scaling factor
    if (scaling_factor_ == 2) {
        realcugan_->prepadding = 18;
    }
    if (scaling_factor_ == 3) {
        realcugan_->prepadding = 14;
    }
    if (scaling_factor_ == 4) {
        realcugan_->prepadding = 19;
    }

    // Calculate tilesize based on GPU heap budget
    uint32_t heap_budget = ncnn::get_gpu_device(gpuid_)->get_heap_budget();
    if (scaling_factor_ == 2) {
        if (heap_budget > 1300) {
            realcugan_->tilesize = 400;
        } else if (heap_budget > 800) {
            realcugan_->tilesize = 300;
        } else if (heap_budget > 400) {
            realcugan_->tilesize = 200;
        } else if (heap_budget > 200) {
            realcugan_->tilesize = 100;
        } else {
            realcugan_->tilesize = 32;
        }
    }
    if (scaling_factor_ == 3) {
        if (heap_budget > 3300) {
            realcugan_->tilesize = 400;
        } else if (heap_budget > 1900) {
            realcugan_->tilesize = 300;
        } else if (heap_budget > 950) {
            realcugan_->tilesize = 200;
        } else if (heap_budget > 320) {
            realcugan_->tilesize = 100;
        } else {
            realcugan_->tilesize = 32;
        }
    }
    if (scaling_factor_ == 4) {
        if (heap_budget > 1690) {
            realcugan_->tilesize = 400;
        } else if (heap_budget > 980) {
            realcugan_->tilesize = 300;
        } else if (heap_budget > 530) {
            realcugan_->tilesize = 200;
        } else if (heap_budget > 240) {
            realcugan_->tilesize = 100;
        } else {
            realcugan_->tilesize = 32;
        }
    }

    // The very-rough sampler requires at least three 32-pixel rows and
    // columns. On a long, narrow frame it would otherwise have no samples.
    if (realcugan_->syncgap == 3 &&
        (dec_ctx->width <= 64 || dec_ctx->height <= 64) &&
        std::max(dec_ctx->width, dec_ctx->height) > realcugan_->tilesize) {
        logger()->info("Real-CUGAN uses sync gap 2 for a narrow tiled frame");
        realcugan_->syncgap = 2;
    }

    return 0;
}

int FilterRealcugan::filter(AVFrame* in_frame, AVFrame** out_frame) {
    int ret;

    if (in_frame->format == AV_PIX_FMT_GBRP16LE && out_pix_fmt_ == AV_PIX_FMT_GBRP16LE) {
        ncnn::Mat input = conversions::gbrp16_to_ncnn_float(in_frame);
        if (input.empty()) return AVERROR(ENOMEM);
        ncnn::Mat output(input.w * realcugan_->scale, input.h * realcugan_->scale, 3, (size_t)4u);
        if (output.empty()) return AVERROR(ENOMEM);
        ret = realcugan_->process_float(input, output);
        if (ret != 0) return ret;
        *out_frame = conversions::ncnn_float_to_gbrp16(output);
        if (!*out_frame) return AVERROR(ENOMEM);
        av_frame_copy_props(*out_frame, in_frame);
        (*out_frame)->pts = av_rescale_q(in_frame->pts, in_time_base_, out_time_base_);
        return 0;
    }

    // Convert the input frame to RGB24
    ncnn::Mat in_mat = conversions::avframe_to_ncnn_mat(in_frame);
    if (in_mat.empty()) {
        logger()->error("Failed to convert AVFrame to ncnn::Mat");
        return -1;
    }

    // Pro models were trained with input compressed to [0.15, 0.85].
    // Apply this for the packed 8-bit path as well as process_float, including
    // the synchronized tiled variants, then undo it after inference.
    if (realcugan_->pro_mode) {
        auto* pixels = static_cast<unsigned char*>(in_mat.data);
        const size_t count = static_cast<size_t>(in_mat.w) * in_mat.h * 3;
        for (size_t i = 0; i < count; ++i)
            pixels[i] = static_cast<unsigned char>(std::lround(pixels[i] * 0.7f + 38.25f));
    }

    // Allocate space for output ncnn::Mat
    int output_width = in_mat.w * realcugan_->scale;
    int output_height = in_mat.h * realcugan_->scale;
    ncnn::Mat out_mat = ncnn::Mat(output_width, output_height, static_cast<size_t>(3), 3);

    ret = realcugan_->process(in_mat, out_mat);
    if (ret != 0) {
        logger()->error("Real-CUGAN processing failed");
        return ret;
    }

    if (realcugan_->pro_mode) {
        auto* pixels = static_cast<unsigned char*>(out_mat.data);
        const size_t count = static_cast<size_t>(out_mat.w) * out_mat.h * 3;
        for (size_t i = 0; i < count; ++i) {
            const int value = static_cast<int>(std::lround((pixels[i] - 38.25f) / 0.7f));
            pixels[i] = static_cast<unsigned char>(std::clamp(value, 0, 255));
        }
    }

    // Convert ncnn::Mat to AVFrame
    *out_frame = conversions::ncnn_mat_to_avframe(out_mat, out_pix_fmt_);
    if (*out_frame == nullptr) {
        logger()->error("Failed to convert ncnn::Mat to AVFrame");
        return AVERROR(ENOMEM);
    }

    // Rescale PTS to encoder's time base
    (*out_frame)->pts = av_rescale_q(in_frame->pts, in_time_base_, out_time_base_);

    // Return the processed frame to the caller
    return ret;
}

void FilterRealcugan::get_output_dimensions(
    const ProcessorConfig&,
    int in_width,
    int in_height,
    int& out_width,
    int& out_height
) const {
    out_width = in_width * scaling_factor_;
    out_height = in_height * scaling_factor_;
}

}  // namespace processors
}  // namespace video2x
