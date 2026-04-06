import { useRef, useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView, Dimensions, Animated,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';

const { width } = Dimensions.get('window');

export const ONBOARDING_KEY = 'roam_onboarding_done';

const SLIDES = [
  {
    title: 'Your journey,\ncrafted for you',
    subtitle: 'Tell us where you\'re headed and what you love — we\'ll build the perfect day.',
    accent: '#3B82F6',
  },
  {
    title: 'Smarter than\na search bar',
    subtitle: 'Our ML model ranks thousands of places against your exact pace, budget, and interests.',
    accent: '#8B5CF6',
  },
  {
    title: 'Routes that\nmake sense',
    subtitle: 'Stops are ordered to minimise walking. Every tip is sourced from locals and travellers.',
    accent: '#10B981',
  },
];

// ── Geometric illustrations — one per slide ───────────────────────────────────

// Slide 1: Destination pin — concentric rings with a dot
function IllustrationJourney({ accent }: { accent: string }) {
  return (
    <View style={ill.container}>
      {/* Outer ring */}
      <View style={[ill.ring, { width: 160, height: 160, borderRadius: 80, borderColor: accent + '18' }]} />
      {/* Mid ring */}
      <View style={[ill.ring, { width: 110, height: 110, borderRadius: 55, borderColor: accent + '30' }]} />
      {/* Inner ring */}
      <View style={[ill.ring, { width: 64, height: 64, borderRadius: 32, borderColor: accent + '55' }]} />
      {/* Centre dot */}
      <View style={[ill.dot, { backgroundColor: accent }]} />
      {/* Pulse halo */}
      <View style={[ill.halo, { borderColor: accent, width: 28, height: 28, borderRadius: 14 }]} />
    </View>
  );
}

// Slide 2: ML bars — three horizontal bars like a ranking chart
function IllustrationML({ accent }: { accent: string }) {
  const bars = [
    { width: 140, opacity: 1.0 },
    { width: 100, opacity: 0.6 },
    { width: 68,  opacity: 0.35 },
  ];
  return (
    <View style={[ill.container, { gap: 14, alignItems: 'flex-start', paddingHorizontal: 24 }]}>
      {bars.map((b, i) => (
        <View key={i} style={{ gap: 6 }}>
          <LinearGradient
            colors={[accent, accent + '55']}
            start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }}
            style={[ill.bar, { width: b.width, opacity: b.opacity }]}
          />
          {/* Label lines */}
          <View style={[ill.labelLine, { width: b.width * 0.6, opacity: b.opacity * 0.4 }]} />
        </View>
      ))}
    </View>
  );
}

// Slide 3: Route — three dots connected by a vertical dashed line
function IllustrationRoute({ accent }: { accent: string }) {
  const stops = [
    { size: 18, label: '09:00' },
    { size: 14, label: '11:30' },
    { size: 14, label: '14:00' },
  ];
  return (
    <View style={[ill.container, { gap: 0, alignItems: 'center' }]}>
      {stops.map((s, i) => (
        <View key={i} style={{ alignItems: 'center' }}>
          {/* Connector line above (except first) */}
          {i > 0 && (
            <View style={[ill.connector, { borderColor: accent + '40' }]} />
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
            <View style={[
              ill.routeDot,
              {
                width: s.size, height: s.size, borderRadius: s.size / 2,
                backgroundColor: i === 0 ? accent : 'transparent',
                borderColor: accent,
                borderWidth: i === 0 ? 0 : 2,
              },
            ]} />
            <View style={[ill.routeLabel, { width: i === 0 ? 80 : 60, opacity: i === 0 ? 0.9 : 0.45 }]} />
          </View>
        </View>
      ))}
    </View>
  );
}

const ILLUSTRATIONS = [IllustrationJourney, IllustrationML, IllustrationRoute];

const ill = StyleSheet.create({
  container: { width: 180, height: 160, alignItems: 'center', justifyContent: 'center' },
  ring: { position: 'absolute', borderWidth: 1 },
  dot:  { width: 14, height: 14, borderRadius: 7, position: 'absolute' },
  halo: { position: 'absolute', borderWidth: 2, opacity: 0.4 },
  bar:  { height: 12, borderRadius: 6 },
  labelLine: { height: 4, backgroundColor: '#fff', borderRadius: 2 },
  connector: { width: 0, height: 28, borderLeftWidth: 2, borderStyle: 'dashed' },
  routeDot:  {},
  routeLabel: { height: 8, backgroundColor: 'rgba(255,255,255,0.15)', borderRadius: 4 },
});

// ── Main screen ───────────────────────────────────────────────────────────────

export default function OnboardingScreen() {
  const router = useRouter();
  const scrollRef = useRef<ScrollView>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const dotAnims = useRef(SLIDES.map((_, i) => new Animated.Value(i === 0 ? 1 : 0))).current;

  const goTo = (index: number) => {
    scrollRef.current?.scrollTo({ x: index * width, animated: true });
    dotAnims.forEach((anim, i) => {
      Animated.timing(anim, { toValue: i === index ? 1 : 0, duration: 200, useNativeDriver: false }).start();
    });
    setActiveIndex(index);
  };

  const handleScroll = (e: any) => {
    const index = Math.round(e.nativeEvent.contentOffset.x / width);
    if (index !== activeIndex) goTo(index);
  };

  const finish = async () => {
    await AsyncStorage.setItem(ONBOARDING_KEY, '1');
    router.replace('/');
  };

  const slide = SLIDES[activeIndex];

  return (
    <SafeAreaView style={styles.container}>
      {/* Top bar */}
      <View style={styles.topBar}>
        <Text style={styles.wordmark}>roam</Text>
        {activeIndex < SLIDES.length - 1 ? (
          <TouchableOpacity onPress={finish} style={styles.skipBtn}>
            <Text style={styles.skipText}>Skip</Text>
          </TouchableOpacity>
        ) : <View style={{ width: 44 }} />}
      </View>

      {/* Slides */}
      <ScrollView
        ref={scrollRef}
        horizontal pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={handleScroll}
        scrollEventThrottle={16}
        style={{ flex: 1 }}
      >
        {SLIDES.map((s, i) => {
          const Illustration = ILLUSTRATIONS[i];
          return (
            <View key={i} style={[styles.slide, { width }]}>
              {/* Background glow */}
              <View style={[styles.slideGlow, { backgroundColor: s.accent }]} />

              {/* Illustration */}
              <View style={[styles.illustrationWrap, { borderColor: s.accent + '20' }]}>
                <Illustration accent={s.accent} />
              </View>

              {/* Slide number */}
              <Text style={[styles.slideNum, { color: s.accent }]}>0{i + 1}</Text>

              <Text style={styles.slideTitle}>{s.title}</Text>
              <Text style={styles.slideSubtitle}>{s.subtitle}</Text>
            </View>
          );
        })}
      </ScrollView>

      {/* Bottom */}
      <View style={styles.bottom}>
        <View style={styles.dots}>
          {SLIDES.map((s, i) => (
            <Animated.View key={i} style={[
              styles.dot,
              {
                backgroundColor: dotAnims[i].interpolate({
                  inputRange: [0, 1], outputRange: ['rgba(255,255,255,0.15)', s.accent],
                }),
                width: dotAnims[i].interpolate({
                  inputRange: [0, 1], outputRange: [6, 24],
                }),
              },
            ]} />
          ))}
        </View>

        <TouchableOpacity
          style={[styles.btn, { backgroundColor: slide.accent }]}
          onPress={activeIndex < SLIDES.length - 1 ? () => goTo(activeIndex + 1) : finish}
        >
          <Text style={styles.btnText}>
            {activeIndex < SLIDES.length - 1 ? 'Continue' : 'Get started'}
          </Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },

  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 24, paddingTop: 8, paddingBottom: 4,
  },
  wordmark: { color: '#fff', fontSize: 22, fontWeight: '800', letterSpacing: -1 },
  skipBtn:  { paddingHorizontal: 4, paddingVertical: 6 },
  skipText: { color: 'rgba(255,255,255,0.3)', fontSize: 14 },

  slide: {
    flex: 1, alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 40, gap: 20,
  },
  slideGlow: {
    position: 'absolute', width: 280, height: 280, borderRadius: 140,
    opacity: 0.06, top: '10%',
  },
  illustrationWrap: {
    width: 200, height: 200, borderRadius: 40,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1,
    backgroundColor: 'rgba(255,255,255,0.02)',
    marginBottom: 8,
  },
  slideNum:      { fontSize: 12, fontWeight: '700', letterSpacing: 2, opacity: 0.8 },
  slideTitle:    { color: '#fff', fontSize: 34, fontWeight: '800', textAlign: 'center', letterSpacing: -1, lineHeight: 40 },
  slideSubtitle: { color: 'rgba(255,255,255,0.45)', fontSize: 16, textAlign: 'center', lineHeight: 25 },

  bottom:  { paddingHorizontal: 24, paddingBottom: 36, gap: 24, alignItems: 'center' },
  dots:    { flexDirection: 'row', gap: 6, alignItems: 'center' },
  dot:     { height: 6, borderRadius: 3 },
  btn:     { width: '100%', paddingVertical: 18, borderRadius: 14, alignItems: 'center' },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
});
