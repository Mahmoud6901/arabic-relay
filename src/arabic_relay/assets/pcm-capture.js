// Arabic Relay: microphone samples for processing during speech. Runs in an AudioContext created at 16 kHz,
// so the browser resamples the microphone; the node is configured for one (downmixed) input channel.
// Posts signed 16-bit frames of 1,600 samples (100 ms). Audio never leaves the page except to Relay's own server.
class RelayPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Int16Array(1600);
    this.fill = 0;
    this.stopped = false;
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        this.send(true);
        this.port.postMessage({type: 'flushed'});
      } else if (event.data === 'stop') {
        this.stopped = true;
      }
    };
  }

  send(partial) {
    if (!this.fill) return;
    const samples = partial ? this.frame.slice(0, this.fill) : this.frame;
    this.port.postMessage({type: 'pcm', samples}, [samples.buffer]);
    this.frame = new Int16Array(1600);
    this.fill = 0;
  }

  process(inputs) {
    if (this.stopped) return false;
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) {
        const sample = Math.max(-1, Math.min(1, channel[i]));
        this.frame[this.fill++] = sample < 0 ? sample * 32768 : sample * 32767;
        if (this.fill === this.frame.length) this.send(false);
      }
    }
    return true;
  }
}

registerProcessor('relay-pcm-capture', RelayPcmCapture);
