package com.herta.aether_fm_s24

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import com.ryanheise.audioservice.AudioServiceActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : AudioServiceActivity() {
    private val CHANNEL = "aether_fm/car_bluetooth_sco"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            when (call.method) {
                "startBluetoothSco" -> {
                    try {
                        // Only engage Bluetooth SCO if a Bluetooth SCO headset/car mic is actually connected!
                        // Otherwise keep normal routing so the phone's built-in mic works properly.
                        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS)
                        val hasBtSco = devices.any {
                            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                            it.type == AudioDeviceInfo.TYPE_BLE_HEADSET
                        }
                        if (hasBtSco && audioManager.isBluetoothScoAvailableOffCall) {
                            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
                            @Suppress("DEPRECATION")
                            audioManager.startBluetoothSco()
                            @Suppress("DEPRECATION")
                            audioManager.isBluetoothScoOn = true
                            result.success(true)
                        } else {
                            result.success(false)
                        }
                    } catch (e: Exception) {
                        result.success(false)
                    }
                }
                "stopBluetoothSco" -> {
                    try {
                        @Suppress("DEPRECATION")
                        if (audioManager.isBluetoothScoOn) {
                            audioManager.isBluetoothScoOn = false
                            audioManager.stopBluetoothSco()
                        }
                        audioManager.mode = AudioManager.MODE_NORMAL
                        result.success(true)
                    } catch (e: Exception) {
                        result.success(false)
                    }
                }
                else -> result.notImplemented()
            }
        }
    }
}
