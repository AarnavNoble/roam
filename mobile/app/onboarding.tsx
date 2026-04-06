import { useRef, useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView, Dimensions, Animated,
} from 'react-native';
import { useRouter } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';

const { width } = Dimensions.get('window');

export const ONBOARDING_KEY = 'roam_onboarding_done';

const SLIDES = [
  {
    emoji: '🗺️',
    title: 'Your journey,\ncrafted for you',
    subtitle: 'Tell us where you\'re headed and what you love — we\'ll build the perfect day.',
    accent: '#3B82F6',
  },
  {
    emoji: '🤖',
    title: 'Smarter than\na search bar',
    subtitle: 'Our ML model ranks thousands of places against your exact pace, budget, and interests.',
    accent: '#8B5CF6',
  },
  {
    emoji: '📍',
    title: 'Routes that\nmake sense',
    subtitle: 'Stops are ordered to minimise walking. Every tip is sourced from locals and travellers.',
    accent: '#10B981',
  },
];

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
      {/* Skip */}
      <View style={styles.topBar}>
        <View style={{ width: 48 }} />
        <View style={{ flex: 1 }} />
        {activeIndex < SLIDES.length - 1 ? (
          <TouchableOpacity onPress={finish} style={styles.skipBtn}>
            <Text style={styles.skipText}>Skip</Text>
          </TouchableOpacity>
        ) : <View style={{ width: 48 }} />}
      </View>

      {/* Slides */}
      <ScrollView
        ref={scrollRef}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={handleScroll}
        scrollEventThrottle={16}
        style={{ flex: 1 }}
      >
        {SLIDES.map((s, i) => (
          <View key={i} style={[styles.slide, { width }]}>
            {/* Glow */}
            <View style={[styles.slideGlow, { backgroundColor: s.accent }]} />

            {/* Emoji hero */}
            <View style={[styles.emojiContainer, { borderColor: s.accent + '30', backgroundColor: s.accent + '10' }]}>
              <Text style={styles.emoji}>{s.emoji}</Text>
            </View>

            <Text style={styles.slideTitle}>{s.title}</Text>
            <Text style={styles.slideSubtitle}>{s.subtitle}</Text>
          </View>
        ))}
      </ScrollView>

      {/* Bottom */}
      <View style={styles.bottom}>
        {/* Dot indicators */}
        <View style={styles.dots}>
          {SLIDES.map((s, i) => (
            <Animated.View key={i} style={[
              styles.dot,
              {
                backgroundColor: dotAnims[i].interpolate({
                  inputRange: [0, 1], outputRange: ['rgba(255,255,255,0.2)', s.accent],
                }),
                width: dotAnims[i].interpolate({
                  inputRange: [0, 1], outputRange: [6, 20],
                }),
              },
            ]} />
          ))}
        </View>

        {/* CTA */}
        {activeIndex < SLIDES.length - 1 ? (
          <TouchableOpacity style={[styles.btn, { backgroundColor: slide.accent }]} onPress={() => goTo(activeIndex + 1)}>
            <Text style={styles.btnText}>Next</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={[styles.btn, { backgroundColor: slide.accent }]} onPress={finish}>
            <Text style={styles.btnText}>Let's go</Text>
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },

  topBar: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 20, paddingTop: 8, paddingBottom: 4,
  },
  skipBtn: { paddingHorizontal: 12, paddingVertical: 6 },
  skipText: { color: 'rgba(255,255,255,0.35)', fontSize: 14 },

  slide: {
    flex: 1, alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 40, gap: 24,
  },
  slideGlow: {
    position: 'absolute', width: 300, height: 300, borderRadius: 150,
    opacity: 0.07, top: '15%',
  },
  emojiContainer: {
    width: 120, height: 120, borderRadius: 36,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, marginBottom: 8,
  },
  emoji:         { fontSize: 52 },
  slideTitle:    { color: '#fff', fontSize: 34, fontWeight: '800', textAlign: 'center', letterSpacing: -1, lineHeight: 40 },
  slideSubtitle: { color: 'rgba(255,255,255,0.45)', fontSize: 16, textAlign: 'center', lineHeight: 24 },

  bottom: { paddingHorizontal: 24, paddingBottom: 32, gap: 24, alignItems: 'center' },
  dots:   { flexDirection: 'row', gap: 6, alignItems: 'center' },
  dot:    { height: 6, borderRadius: 3 },

  btn: {
    width: '100%', paddingVertical: 18,
    borderRadius: 14, alignItems: 'center',
  },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
});
