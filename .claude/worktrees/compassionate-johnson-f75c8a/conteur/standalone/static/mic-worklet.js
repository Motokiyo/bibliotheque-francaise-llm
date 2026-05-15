// AudioWorklet processor: captures mic at AudioContext sample rate (24000 Hz),
// converts Float32 to Int16 PCM little-endian, posts ArrayBuffer chunks of ~40ms.

class MicPCM16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Int16Array(960); // 40ms at 24kHz
    this._idx = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const channel = input[0];
    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      this._buffer[this._idx++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this._idx === this._buffer.length) {
        this.port.postMessage(this._buffer.buffer.slice(0));
        this._idx = 0;
      }
    }
    return true;
  }
}
registerProcessor("mic-pcm16-processor", MicPCM16Processor);
