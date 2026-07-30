#!/usr/bin/env python3
"""Listet ALLE PyAudio-Geraete mit Details (Input-/Output-Kanaele, Sample-Rate)."""

import pyaudio

pa = pyaudio.PyAudio()

print(f"Anzahl Geraete: {pa.get_device_count()}")
print()

for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    print(f"Index {i}: {info['name']}")
    print(f"  maxInputChannels:  {info['maxInputChannels']}")
    print(f"  maxOutputChannels: {info['maxOutputChannels']}")
    print(f"  defaultSampleRate: {info['defaultSampleRate']}")
    print(f"  hostApi: {pa.get_host_api_info_by_index(info['hostApi'])['name']}")
    print()

try:
    default_input = pa.get_default_input_device_info()
    print(f"Default Input Device: {default_input['name']} (Index {default_input['index']})")
except OSError as e:
    print(f"Kein Default Input Device: {e}")

pa.terminate()
