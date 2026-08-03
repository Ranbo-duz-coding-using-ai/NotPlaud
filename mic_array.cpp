#include "mic_array.h"

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "config.h"

// Steering angle for voice-isolation mode, in degrees off the array's
// broadside axis. 0 means "the person is straight in front of the mic face",
// which is the normal case for a pill on a table or clipped to a shirt.
#ifndef VOICE_STEER_DEG
#define VOICE_STEER_DEG 0.0f
#endif

#define SPEED_OF_SOUND 343.0f
#define MIC_COUNT 4
#define DELAY_LINE_LEN 16

static i2s_chan_handle_t rxA = nullptr;
static i2s_chan_handle_t rxB = nullptr;
static CaptureMode activeMode = MODE_STANDARD;
static float lastLevel = 0.0f;

// Raw interleaved 32-bit stereo frames from each bus.
static int32_t bufA[I2S_FRAMES_PER_READ * 2];
static int32_t bufB[I2S_FRAMES_PER_READ * 2];

// Per-mic fractional delay lines for the beamformer.
static float delayLine[MIC_COUNT][DELAY_LINE_LEN];
static int delayPos[MIC_COUNT];
static float steerDelay[MIC_COUNT];  // in samples

// DC blocker state (one-pole high-pass, y[n] = x[n] - x[n-1] + R*y[n-1]).
static float dcX1 = 0.0f, dcY1 = 0.0f;

// Speech high-pass state for voice-isolation mode.
static float hpX1 = 0.0f, hpY1 = 0.0f;

// Noise-floor tracker and gate envelope.
static float noiseFloor = 0.0f;
static float gateEnv = 1.0f;

CaptureMode micModeFromName(const char *name) {
  if (!name) return MODE_STANDARD;
  if (strcmp(name, "wide-spectrum") == 0) return MODE_WIDE_SPECTRUM;
  if (strcmp(name, "voice-isolation") == 0) return MODE_VOICE_ISOLATION;
  return MODE_STANDARD;
}

const char *micModeName(CaptureMode mode) {
  switch (mode) {
    case MODE_WIDE_SPECTRUM: return "wide-spectrum";
    case MODE_VOICE_ISOLATION: return "voice-isolation";
    default: return "standard";
  }
}

static void computeSteering() {
  // Linear array: mic i sits at i * MIC_SPACING_M along the axis. A source at
  // `theta` off broadside reaches each mic at a slightly different time; we
  // delay the earlier mics so all four line up before summing.
  const float theta = VOICE_STEER_DEG * (float)M_PI / 180.0f;
  float maxDelay = 0.0f;
  for (int i = 0; i < MIC_COUNT; i++) {
    float seconds = (i * MIC_SPACING_M * sinf(theta)) / SPEED_OF_SOUND;
    steerDelay[i] = seconds * (float)SAMPLE_RATE;
    if (steerDelay[i] > maxDelay) maxDelay = steerDelay[i];
  }
  // Shift so every delay is >= 0 (we can only delay, never advance).
  for (int i = 0; i < MIC_COUNT; i++) {
    steerDelay[i] = maxDelay - steerDelay[i];
    if (steerDelay[i] < 0) steerDelay[i] = 0;
    if (steerDelay[i] > DELAY_LINE_LEN - 2) steerDelay[i] = DELAY_LINE_LEN - 2;
  }
}

static bool startChannel(i2s_port_t port, int bclk, int ws, int din, i2s_chan_handle_t *out) {
  i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(port, I2S_ROLE_MASTER);
  chanCfg.auto_clear = true;
  if (i2s_new_channel(&chanCfg, nullptr, out) != ESP_OK) return false;

  i2s_std_config_t stdCfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)bclk,
          .ws = (gpio_num_t)ws,
          .dout = I2S_GPIO_UNUSED,
          .din = (gpio_num_t)din,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };

  if (i2s_channel_init_std_mode(*out, &stdCfg) != ESP_OK) return false;
  return i2s_channel_enable(*out) == ESP_OK;
}

bool micArrayBegin() {
  memset(delayLine, 0, sizeof(delayLine));
  memset(delayPos, 0, sizeof(delayPos));
  computeSteering();

  if (!startChannel(I2S_NUM_0, I2S_A_BCLK, I2S_A_WS, I2S_A_DIN, &rxA)) {
    Serial.println("[mic] I2S bus A failed to start");
    return false;
  }
  if (!startChannel(I2S_NUM_1, I2S_B_BCLK, I2S_B_WS, I2S_B_DIN, &rxB)) {
    Serial.println("[mic] I2S bus B failed to start");
    return false;
  }
  Serial.println("[mic] 4-mic array running");
  return true;
}

void micArrayEnd() {
  if (rxA) {
    i2s_channel_disable(rxA);
    i2s_del_channel(rxA);
    rxA = nullptr;
  }
  if (rxB) {
    i2s_channel_disable(rxB);
    i2s_del_channel(rxB);
    rxB = nullptr;
  }
}

void micArraySetMode(CaptureMode mode) {
  activeMode = mode;
  // Reset the adaptive pieces so a mode change takes effect immediately
  // instead of inheriting the previous mode's noise estimate.
  noiseFloor = 0.0f;
  gateEnv = 1.0f;
  Serial.printf("[mic] mode -> %s\n", micModeName(mode));
}

CaptureMode micArrayGetMode() { return activeMode; }

float micArrayLevel() { return lastLevel; }

// Reads one sample delayed by `samples` (fractional) from mic `mic`.
static inline float readDelayed(int mic, float samples) {
  int whole = (int)samples;
  float frac = samples - whole;
  int base = delayPos[mic] - whole;
  while (base < 0) base += DELAY_LINE_LEN;
  int prev = base - 1;
  if (prev < 0) prev += DELAY_LINE_LEN;
  return delayLine[mic][base] * (1.0f - frac) + delayLine[mic][prev] * frac;
}

size_t micArrayRead(int16_t *out, size_t maxSamples) {
  if (!rxA || !rxB) return 0;

  const size_t wantBytes = I2S_FRAMES_PER_READ * 2 * sizeof(int32_t);
  size_t gotA = 0, gotB = 0;

  if (i2s_channel_read(rxA, bufA, wantBytes, &gotA, pdMS_TO_TICKS(100)) != ESP_OK) return 0;
  if (i2s_channel_read(rxB, bufB, wantBytes, &gotB, pdMS_TO_TICKS(100)) != ESP_OK) return 0;

  size_t frames = min(gotA, gotB) / (2 * sizeof(int32_t));
  if (frames > maxSamples) frames = maxSamples;

  float peak = 0.0f;

  for (size_t i = 0; i < frames; i++) {
    // Bus A carries mics 1/2, bus B carries mics 3/4.
    float mic[MIC_COUNT];
    mic[0] = (float)(bufA[i * 2 + 0] >> MIC_SHIFT);
    mic[1] = (float)(bufA[i * 2 + 1] >> MIC_SHIFT);
    mic[2] = (float)(bufB[i * 2 + 0] >> MIC_SHIFT);
    mic[3] = (float)(bufB[i * 2 + 1] >> MIC_SHIFT);

    float mixed = 0.0f;

    if (activeMode == MODE_VOICE_ISOLATION) {
      // Push into the delay lines, then sum the time-aligned copies. Summing
      // four mics gives ~6 dB of gain against diffuse room noise, because the
      // speech is correlated across mics and the noise is not.
      for (int m = 0; m < MIC_COUNT; m++) {
        delayPos[m] = (delayPos[m] + 1) % DELAY_LINE_LEN;
        delayLine[m][delayPos[m]] = mic[m];
        mixed += readDelayed(m, steerDelay[m]);
      }
      mixed *= 0.25f;
    } else {
      for (int m = 0; m < MIC_COUNT; m++) mixed += mic[m];
      mixed *= 0.25f;
      if (activeMode == MODE_WIDE_SPECTRUM) {
        // Keep the room in the recording: more gain, no gating, and only the
        // gentlest DC removal so ambience and distant speakers survive.
        mixed *= 1.6f;
      }
    }

    // DC blocker — MEMS mics have a standing offset that eats headroom.
    float dcOut = mixed - dcX1 + 0.995f * dcY1;
    dcX1 = mixed;
    dcY1 = dcOut;
    float sample = dcOut;

    if (activeMode == MODE_VOICE_ISOLATION) {
      // ~120 Hz high-pass: drops HVAC rumble, desk thumps, and handling noise
      // without touching speech fundamentals.
      const float a = 0.955f;
      float hpOut = a * (hpY1 + sample - hpX1);
      hpX1 = sample;
      hpY1 = hpOut;
      sample = hpOut;

      // Track the noise floor and duck anything sitting close to it. Attack is
      // fast so speech onsets are not clipped; release is slow so the gate
      // does not chatter between words.
      float magnitude = fabsf(sample);
      if (noiseFloor == 0.0f) noiseFloor = magnitude;
      noiseFloor = (magnitude < noiseFloor) ? (0.98f * noiseFloor + 0.02f * magnitude)
                                            : (0.9995f * noiseFloor + 0.0005f * magnitude);

      float threshold = noiseFloor * 2.5f;
      float target = (magnitude > threshold) ? 1.0f : 0.25f;
      gateEnv += (target > gateEnv) ? (target - gateEnv) * 0.35f : (target - gateEnv) * 0.02f;
      sample *= gateEnv;
    }

    // Soft clip rather than wrapping around on transients.
    if (sample > 32767.0f) sample = 32767.0f;
    if (sample < -32768.0f) sample = -32768.0f;

    out[i] = (int16_t)sample;

    float normalised = fabsf(sample) / 32768.0f;
    if (normalised > peak) peak = normalised;
  }

  // Smooth the level so the LED does not strobe.
  lastLevel = lastLevel * 0.7f + peak * 0.3f;
  return frames;
}
