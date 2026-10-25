import 'dart:async';
import 'package:flutter/material.dart';
import '../services/radio_audio_handler.dart';
import '../services/websocket_client.dart';

class FmLogoMark extends StatelessWidget {
  final double size;
  const FmLogoMark({super.key, this.size = 32});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: const Color(0xFF0A0A0A),
        border: Border.all(color: const Color(0xFFFFD600), width: 2.0),
      ),
      alignment: Alignment.center,
      child: RichText(
        text: TextSpan(
          style: TextStyle(
            fontFamily: 'monospace',
            fontWeight: FontWeight.w900,
            fontSize: size * 0.44,
            letterSpacing: -0.5,
          ),
          children: const [
            TextSpan(text: 'F', style: TextStyle(color: Color(0xFFFFD600))),
            TextSpan(text: 'M', style: TextStyle(color: Color(0xFFF5F5F5))),
          ],
        ),
      ),
    );
  }
}

class BoothScreen extends StatefulWidget {
  final AetherWebSocketClient wsClient;
  final AetherRadioAudioHandler audioHandler;

  const BoothScreen({
    super.key,
    required this.wsClient,
    required this.audioHandler,
  });

  @override
  State<BoothScreen> createState() => _BoothScreenState();
}

class _BoothScreenState extends State<BoothScreen> {
  String _title = 'INITIALIZING DECK A';
  String _artist = 'FM BROADCAST';
  String _coverUrl = '';
  String _trackMood = 'STUDIO GROOVE';
  String _activeCardId = 'card_herta_overdrive';
  String _activeCardTitle = 'CH-01 // OVERDRIVE PROTOCOL';
  String _upNextLabel = 'STANDBY BUFFER...';
  bool _upNextReady = false;
  bool _standbyPaused = false;

  String _hertaTranscript = 'SIGNAL LOCKED // 48KHZ MASTER CLOCK ONLINE';
  bool _djSpeaking = false;
  bool _isCallingBooth = false;
  bool _isPlaying = true;
  bool _starredCurrent = false;

  double _elapsedSec = 0.0;
  double _durationSec = 200.0;
  String _activeVoiceId = 'the_herta';
  String _activeVoiceName = 'CH-01 // CRISP STUDIO';
  String _streamTier = 'mobile_saver';
  Timer? _uiCallSafetyTimer;

  static const Color kBg = Color(0xFF0A0A0A);
  static const Color kPanel = Color(0xFF121212);
  static const Color kBorder = Color(0xFF282828);
  static const Color kYellow = Color(0xFFFFD600);
  static const Color kText = Color(0xFFF5F5F5);
  static const Color kMuted = Color(0xFF8A8A8A);

  static const List<Map<String, String>> _moodCards = [
    {
      'card_id': 'card_herta_overdrive',
      'title': 'CH-01 // OVERDRIVE PROTOCOL',
      'subtitle': '125-180 BPM // ELECTRONIC, J-ROCK & DANCE',
    },
    {
      'card_id': 'card_space_station_nocturne',
      'title': 'CH-02 // MIDNIGHT TELEMETRY',
      'subtitle': '85-130 BPM // SYNTH, POP & LATE-NIGHT SIGNAL',
    },
    {
      'card_id': 'card_simulated_chaos',
      'title': 'CH-03 // FULL SPECTRUM',
      'subtitle': 'ALL BPMS // COMPLETE 121-TRACK VAULT ROTATION',
    },
  ];

  static const List<Map<String, String>> _voiceProfiles = [
    {'id': 'the_herta', 'name': 'CH-01 // CRISP STUDIO'},
    {'id': 'velvet_nocturne', 'name': 'CH-02 // VELVET NOCTURNE'},
    {'id': 'cyber_silver', 'name': 'CH-03 // FAST SIGNAL'},
    {'id': 'astral_himeko', 'name': 'CH-04 // WARM ANALOG'},
    {'id': 'british_spires', 'name': 'CH-05 // UK BROADCAST'},
    {'id': 'midnight_onyx', 'name': 'CH-06 // LOW BARITONE'},
  ];

  @override
  void initState() {
    super.initState();
    widget.wsClient.events.listen((event) {
      if (!mounted) return;
      final type = event['event'];
      setState(() {
        if (type == 'NOW_PLAYING' ||
            type == 'STATE' ||
            type == 'STUDIO_TELEMETRY' ||
            type == 'UP_NEXT' ||
            type == 'MOOD_CHANGED' ||
            type == 'VOICE_CHANGED') {
          final prevTitle = _title;
          _title = event['title'] ?? _title;
          if (_title != prevTitle) {
            _starredCurrent = false;
          }
          _artist = event['artist'] ?? _artist;
          _coverUrl = event['cover_url'] ?? _coverUrl;
          if (event['track_mood'] != null &&
              (event['track_mood'] as String).isNotEmpty) {
            _trackMood = (event['track_mood'] as String).toUpperCase();
          }
          _standbyPaused = event['standby_paused'] == true;
          _activeCardTitle =
              (event['card'] ?? _activeCardTitle).toString().toUpperCase();
          _activeCardId = event['card_id'] ?? _activeCardId;
          _elapsedSec = (event['elapsed_sec'] as num?)?.toDouble() ?? _elapsedSec;
          _durationSec =
              (event['duration_sec'] as num?)?.toDouble() ?? _durationSec;
          _djSpeaking = event['speaking'] == true;
          if (event['last_transcript'] != null &&
              (event['last_transcript'] as String).isNotEmpty) {
            _hertaTranscript = event['last_transcript'];
          }
          if (event['active_voice_id'] != null) {
            _activeVoiceId = event['active_voice_id'];
          }
          if (event['active_voice_name'] != null) {
            _activeVoiceName = event['active_voice_name'];
          }
          final upNext = event['up_next'] as Map<String, dynamic>?;
          _upNextReady = event['up_next_ready'] == true;
          if (upNext != null && (upNext['title'] ?? '').toString().isNotEmpty) {
            _upNextLabel = '${upNext['artist']} // ${upNext['title']}';
          } else {
            _upNextLabel = 'SELECTING NEXT TRACK...';
          }
        } else if (type == 'DJ_STATE') {
          _djSpeaking = event['speaking'] == true;
          if (event['transcript'] != null &&
              (event['transcript'] as String).isNotEmpty) {
            _hertaTranscript = event['transcript'];
          }
        } else if (type == 'CALL_MIC_OPEN') {
          _isCallingBooth = true;
          _uiCallSafetyTimer?.cancel();
          _uiCallSafetyTimer = Timer(const Duration(seconds: 6), () {
            if (mounted && _isCallingBooth) {
              setState(() => _isCallingBooth = false);
              widget.wsClient.sendJson({'event': 'CALL_STOP'});
            }
          });
        } else if (type == 'CALL_MIC_CLOSE') {
          _uiCallSafetyTimer?.cancel();
          _isCallingBooth = false;
        }
      });
    });
  }

  @override
  void dispose() {
    _uiCallSafetyTimer?.cancel();
    super.dispose();
  }

  String _formatSeconds(double sec) {
    final total = sec.isFinite && sec > 0 ? sec.floor() : 0;
    final mins = total ~/ 60;
    final rem = total % 60;
    return '${mins < 10 ? '0' : ''}$mins:${rem < 10 ? '0' : ''}$rem';
  }

  void _toggleVoiceCall() {
    if (_isCallingBooth) {
      // Tap a second time to finish & submit immediately!
      _uiCallSafetyTimer?.cancel();
      setState(() => _isCallingBooth = false);
      widget.wsClient.sendJson({'event': 'CALL_STOP'});
    } else {
      widget.wsClient.sendJson({
        'event': 'CALL_START',
        'sample_rate': 16000,
      });
    }
  }

  void _openSongRequestDialog() {
    final controller = TextEditingController();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kPanel,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.zero),
      builder: (ctx) {
        return Padding(
          padding: EdgeInsets.only(
            left: 20,
            right: 20,
            top: 20,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'DIRECT TRACK / ARTIST REQUEST',
                style: TextStyle(
                  fontFamily: 'monospace',
                  color: kYellow,
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 1.5,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: controller,
                autofocus: true,
                style: const TextStyle(color: kText, fontSize: 14),
                decoration: const InputDecoration(
                  hintText: 'ENTER SONG TITLE OR ARTIST...',
                  hintStyle: TextStyle(color: kMuted, fontSize: 12),
                  filled: true,
                  fillColor: kBg,
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.zero,
                    borderSide: BorderSide(color: kBorder),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.zero,
                    borderSide: BorderSide(color: kYellow),
                  ),
                ),
                onSubmitted: (val) {
                  if (val.trim().isNotEmpty) {
                    widget.wsClient.sendJson({
                      'event': 'REQUEST_SONG',
                      'query': val.trim(),
                      'play_now': false,
                    });
                    Navigator.pop(ctx);
                  }
                },
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: kText,
                        side: const BorderSide(color: kBorder),
                        shape: const RoundedRectangleBorder(
                            borderRadius: BorderRadius.zero),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                      ),
                      onPressed: () {
                        final q = controller.text.trim();
                        if (q.isNotEmpty) {
                          widget.wsClient.sendJson({
                            'event': 'REQUEST_SONG',
                            'query': q,
                            'play_now': false,
                          });
                          Navigator.pop(ctx);
                        }
                      },
                      child: const Text(
                        'QUEUE NEXT',
                        style: TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: kYellow,
                        foregroundColor: kBg,
                        shape: const RoundedRectangleBorder(
                            borderRadius: BorderRadius.zero),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                      ),
                      onPressed: () {
                        final q = controller.text.trim();
                        if (q.isNotEmpty) {
                          widget.wsClient.sendJson({
                            'event': 'REQUEST_SONG',
                            'query': q,
                            'play_now': true,
                          });
                          Navigator.pop(ctx);
                        }
                      },
                      child: const Text(
                        'PLAY NOW',
                        style: TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  void _openMoodSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: kPanel,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.zero),
      builder: (ctx) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'SELECT PROGRAM PROTOCOL',
                  style: TextStyle(
                    fontFamily: 'monospace',
                    color: kYellow,
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1.5,
                  ),
                ),
                const SizedBox(height: 12),
                ..._moodCards.map((card) {
                  final selected = card['card_id'] == _activeCardId;
                  return InkWell(
                    onTap: () {
                      setState(() {
                        _activeCardId = card['card_id']!;
                        _activeCardTitle = card['title']!;
                      });
                      widget.wsClient.sendJson({
                        'event': 'ACTIVATE_CARD',
                        'card_id': card['card_id'],
                      });
                      Navigator.pop(ctx);
                    },
                    child: Container(
                      width: double.infinity,
                      margin: const EdgeInsets.only(bottom: 8),
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: kBg,
                        border: Border.all(
                          color: selected ? kYellow : kBorder,
                          width: selected ? 1.5 : 1.0,
                        ),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            card['title']!,
                            style: TextStyle(
                              fontFamily: 'monospace',
                              color: selected ? kYellow : kText,
                              fontWeight: FontWeight.bold,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            card['subtitle']!,
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              color: kMuted,
                              fontSize: 10,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                }),
              ],
            ),
          ),
        );
      },
    );
  }

  void _openSettingsSheet() {
    final ipController =
        TextEditingController(text: widget.wsClient.tailscaleHost);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kPanel,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.zero),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setSheetState) {
            return Padding(
              padding: EdgeInsets.only(
                left: 20,
                right: 20,
                top: 20,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
              ),
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'SYSTEM CONFIGURATION // VOICE & STREAM',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        color: kYellow,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 1.5,
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Text(
                      'VOICE SYNTHESIS CHANNEL',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        color: kMuted,
                        fontSize: 10,
                        letterSpacing: 1.2,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: _voiceProfiles.map((vp) {
                        final isSelected = vp['id'] == _activeVoiceId;
                        return InkWell(
                          onTap: () {
                            setSheetState(() => _activeVoiceId = vp['id']!);
                            setState(() {
                              _activeVoiceId = vp['id']!;
                              _activeVoiceName = vp['name']!;
                            });
                            widget.wsClient.sendJson({
                              'event': 'SET_VOICE_PROFILE',
                              'profile_id': vp['id'],
                            });
                          },
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 10, vertical: 8),
                            decoration: BoxDecoration(
                              color: kBg,
                              border: Border.all(
                                color: isSelected ? kYellow : kBorder,
                              ),
                            ),
                            child: Text(
                              vp['name']!,
                              style: TextStyle(
                                fontFamily: 'monospace',
                                color: isSelected ? kYellow : kText,
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                    const SizedBox(height: 18),
                    const Text(
                      'OPUS STREAM BITRATE',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        color: kMuted,
                        fontSize: 10,
                        letterSpacing: 1.2,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        {'id': 'ultra_saver', 'label': '40K SAVER'},
                        {'id': 'mobile_saver', 'label': '64K STANDARD'},
                        {'id': 'wifi_hi_res', 'label': '192K STUDIO'},
                      ].map((t) {
                        final sel = _streamTier == t['id'];
                        return Expanded(
                          child: Padding(
                            padding: const EdgeInsets.only(right: 6),
                            child: InkWell(
                              onTap: () {
                                setSheetState(() => _streamTier = t['id']!);
                                setState(() => _streamTier = t['id']!);
                                widget.audioHandler.startRadioStream(
                                  tier: _streamTier,
                                  forceReload: true,
                                );
                              },
                              child: Container(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 10),
                                alignment: Alignment.center,
                                decoration: BoxDecoration(
                                  color: kBg,
                                  border: Border.all(
                                    color: sel ? kYellow : kBorder,
                                  ),
                                ),
                                child: Text(
                                  t['label']!,
                                  style: TextStyle(
                                    fontFamily: 'monospace',
                                    color: sel ? kYellow : kText,
                                    fontSize: 10,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                    const SizedBox(height: 18),
                    const Text(
                      'HOST NODE IP (LAN OR TAILSCALE)',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        color: kMuted,
                        fontSize: 10,
                        letterSpacing: 1.2,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: ipController,
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              color: kText,
                              fontSize: 13,
                            ),
                            decoration: const InputDecoration(
                              filled: true,
                              fillColor: kBg,
                              contentPadding: EdgeInsets.symmetric(
                                  horizontal: 12, vertical: 12),
                              enabledBorder: OutlineInputBorder(
                                borderRadius: BorderRadius.zero,
                                borderSide: BorderSide(color: kBorder),
                              ),
                              focusedBorder: OutlineInputBorder(
                                borderRadius: BorderRadius.zero,
                                borderSide: BorderSide(color: kYellow),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        ElevatedButton(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: kYellow,
                            foregroundColor: kBg,
                            shape: const RoundedRectangleBorder(
                                borderRadius: BorderRadius.zero),
                            padding: const EdgeInsets.symmetric(
                                horizontal: 18, vertical: 14),
                          ),
                          onPressed: () {
                            final newIp = ipController.text.trim();
                            if (newIp.isNotEmpty) {
                              widget.wsClient.updateHost(newIp);
                              widget.audioHandler
                                  .startRadioStream(forceReload: true);
                              setState(() {});
                              Navigator.pop(ctx);
                            }
                          },
                          child: const Text(
                            'CONNECT',
                            style: TextStyle(
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.bold,
                              fontSize: 11,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final progress = _durationSec > 0
        ? (_elapsedSec / _durationSec).clamp(0.0, 1.0)
        : 0.0;

    return Scaffold(
      backgroundColor: kBg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Column(
            children: [
              // 1. Industrial Header Bar (Overflow-Safe with Expanded Title)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                decoration: BoxDecoration(
                  color: kPanel,
                  border: Border.all(color: kBorder),
                ),
                child: Row(
                  children: [
                    const FmLogoMark(size: 26),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            _standbyPaused
                                ? 'FM // STANDBY (IDLE)'
                                : 'FM // LIVE RECEIVER',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontFamily: 'monospace',
                              color: _standbyPaused ? kMuted : kText,
                              fontSize: 10.5,
                              fontWeight: FontWeight.bold,
                              letterSpacing: 1.1,
                            ),
                          ),
                          Text(
                            '${widget.wsClient.usingRemoteTunnel ? "REMOTE" : "LAN"} • $_activeVoiceName',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              color: kMuted,
                              fontSize: 8.5,
                              letterSpacing: 0.6,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    InkWell(
                      onTap: () {
                        widget.wsClient.sendJson({
                          'event': 'DISCOVER_RELATED',
                          'play_now': true,
                        });
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 7, vertical: 6),
                        decoration: BoxDecoration(
                          border: Border.all(color: kBorder),
                        ),
                        child: const Text(
                          'NEW',
                          style: TextStyle(
                            fontFamily: 'monospace',
                            color: kText,
                            fontSize: 9.5,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 5),
                    InkWell(
                      onTap: _openSongRequestDialog,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 6),
                        decoration: BoxDecoration(
                          border: Border.all(color: kYellow),
                        ),
                        child: const Text(
                          'REQ',
                          style: TextStyle(
                            fontFamily: 'monospace',
                            color: kYellow,
                            fontSize: 9.5,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 5),
                    InkWell(
                      onTap: _openSettingsSheet,
                      child: Container(
                        padding: const EdgeInsets.all(5),
                        decoration: BoxDecoration(
                          border: Border.all(color: kBorder),
                        ),
                        child: const Icon(Icons.tune, color: kText, size: 17),
                      ),
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 10),

              // 2. Responsive Square Album Artwork Frame (Auto-scales to remaining screen height!)
              Expanded(
                child: Center(
                  child: AspectRatio(
                    aspectRatio: 1.0,
                    child: Container(
                      decoration: BoxDecoration(
                        color: kPanel,
                        border: Border.all(
                          color: _djSpeaking ? kYellow : kBorder,
                          width: _djSpeaking ? 2.0 : 1.0,
                        ),
                      ),
                      child: _coverUrl.isNotEmpty
                          ? Image.network(
                              _coverUrl,
                              fit: BoxFit.cover,
                              errorBuilder: (_, __, ___) => _buildFallbackArt(),
                            )
                          : _buildFallbackArt(),
                    ),
                  ),
                ),
              ),

              const SizedBox(height: 10),

              // 3. Track Readout + Song Mood + Progress + Up Next
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: kPanel,
                  border: Border.all(color: kBorder),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'MOOD // $_trackMood',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              color: kYellow,
                              fontSize: 9.5,
                              fontWeight: FontWeight.bold,
                              letterSpacing: 1.0,
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        InkWell(
                          onTap: _openMoodSheet,
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              border: Border.all(color: kBorder),
                            ),
                            child: Text(
                              _activeCardTitle.split('//').last.trim(),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                color: kMuted,
                                fontSize: 8.5,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      _title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: kText,
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      _artist.toUpperCase(),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        color: kMuted,
                        fontSize: 11.5,
                        letterSpacing: 0.8,
                      ),
                    ),
                    const SizedBox(height: 10),
                    LinearProgressIndicator(
                      value: progress,
                      minHeight: 4,
                      backgroundColor: kBg,
                      valueColor:
                          const AlwaysStoppedAnimation<Color>(kYellow),
                    ),
                    const SizedBox(height: 5),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          _formatSeconds(_elapsedSec),
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            color: kMuted,
                            fontSize: 10,
                          ),
                        ),
                        Text(
                          _formatSeconds(_durationSec),
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            color: kMuted,
                            fontSize: 10,
                          ),
                        ),
                      ],
                    ),
                    const Divider(color: kBorder, height: 14),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'NEXT // $_upNextLabel',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              color: kMuted,
                              fontSize: 10,
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            border: Border.all(
                              color: _upNextReady ? kYellow : kBorder,
                            ),
                          ),
                          child: Text(
                            _upNextReady ? 'READY' : 'SYNC',
                            style: TextStyle(
                              fontFamily: 'monospace',
                              color: _upNextReady ? kYellow : kMuted,
                              fontSize: 8.5,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),

              const SizedBox(height: 8),

              // 4. Clean Industrial Telemetry / Voice Readout Strip (Tap to trigger fresh artist intro)
              InkWell(
                onTap: () {
                  widget.wsClient.sendJson({'event': 'ANNOUNCE_NOW'});
                },
                child: Container(
                  width: double.infinity,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: const BoxDecoration(
                    color: kPanel,
                    border: Border(
                      left: BorderSide(color: kYellow, width: 3),
                      top: BorderSide(color: kBorder),
                      right: BorderSide(color: kBorder),
                      bottom: BorderSide(color: kBorder),
                    ),
                  ),
                  child: Text(
                    _hertaTranscript,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      color: kText,
                      fontSize: 10.5,
                      height: 1.3,
                    ),
                  ),
                ),
              ),

              const SizedBox(height: 8),

              // 5. Industrial Transport Bar (Always 100% on-screen)
              Row(
                children: [
                  InkWell(
                    onTap: () {
                      setState(() => _starredCurrent = true);
                      widget.wsClient.sendJson({'event': 'STAR_TRACK'});
                    },
                    child: Container(
                      width: 46,
                      height: 46,
                      decoration: BoxDecoration(
                        color: kPanel,
                        border: Border.all(
                          color: _starredCurrent ? kYellow : kBorder,
                        ),
                      ),
                      alignment: Alignment.center,
                      child: Icon(
                        _starredCurrent ? Icons.bookmark : Icons.bookmark_border,
                        color: _starredCurrent ? kYellow : kText,
                        size: 20,
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  InkWell(
                    onTap: () async {
                      await widget.audioHandler.togglePlayPause();
                      setState(() {
                        _isPlaying = widget.audioHandler.isPlaying;
                      });
                    },
                    child: Container(
                      width: 50,
                      height: 46,
                      decoration: BoxDecoration(
                        color: kPanel,
                        border: Border.all(color: kBorder),
                      ),
                      alignment: Alignment.center,
                      child: Icon(
                        _isPlaying ? Icons.pause : Icons.play_arrow,
                        color: kText,
                        size: 24,
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: InkWell(
                      onTap: _toggleVoiceCall,
                      child: Container(
                        height: 46,
                        decoration: BoxDecoration(
                          color: _isCallingBooth ? kText : kYellow,
                          border: Border.all(
                            color: _isCallingBooth ? kText : kYellow,
                          ),
                        ),
                        alignment: Alignment.center,
                        child: Text(
                          _isCallingBooth
                              ? 'LISTENING [TAP]'
                              : 'VOICE REQUEST',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            color: kBg,
                            fontWeight: FontWeight.w900,
                            fontSize: 11,
                            letterSpacing: 0.8,
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  InkWell(
                    onTap: widget.audioHandler.skipToNext,
                    child: Container(
                      width: 62,
                      height: 46,
                      decoration: BoxDecoration(
                        color: kPanel,
                        border: Border.all(color: kBorder),
                      ),
                      alignment: Alignment.center,
                      child: const Text(
                        'SKIP',
                        style: TextStyle(
                          fontFamily: 'monospace',
                          color: kYellow,
                          fontWeight: FontWeight.bold,
                          fontSize: 11.5,
                          letterSpacing: 1.2,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildFallbackArt() {
    return Container(
      color: kPanel,
      alignment: Alignment.center,
      child: const FmLogoMark(size: 72),
    );
  }
}
