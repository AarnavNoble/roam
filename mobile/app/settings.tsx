import { useState, useCallback, useRef, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, SafeAreaView, ScrollView, Alert, Animated, ActivityIndicator } from 'react-native';
import { useRouter, useFocusEffect } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getSavedTrips, clearPrefs, clearAllSavedTrips, loadPrefs, UserPrefs, User, getCurrentUser, getServerTrips, login, register, logout } from '../services/api';
import { ONBOARDING_KEY } from './onboarding';

const PREFS_LABELS: Record<keyof Omit<UserPrefs, 'goals'>, string> = {
  pace: 'Pace', budget: 'Budget', style: 'Traveling as',
  dietary: 'Dietary', mobility: 'Walking comfort',
  familiarity: 'Familiarity', transport: 'Getting around',
  durationHours: 'Duration', startTime: 'Start time',
};

const VALUE_LABELS: Record<string, string> = {
  relaxed: 'Relaxed', moderate: 'Moderate', packed: 'Packed',
  free: 'Free', budget: 'Budget', mid: 'Mid', splurge: 'Splurge',
  solo: 'Solo', couple: 'Couple', family: 'Family', group: 'Group',
  none: 'None', vegetarian: 'Vegetarian', vegan: 'Vegan', halal: 'Halal', kosher: 'Kosher',
  easy: 'Easy', active: 'Active',
  first_time: 'First time', returning: 'Returning',
  morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening',
  walking: 'Walking', transit: 'Transit', cycling: 'Cycling', driving: 'Driving',
};

const GOAL_COLORS: Record<string, string> = {
  food: '#F59E0B', nature: '#10B981', history: '#8B5CF6',
  culture: '#3B82F6', nightlife: '#EC4899', shopping: '#F97316', adventure: '#EF4444',
};

// Same rules the server applies, checked here so mistakes show up before a request is made
function validateEmail(email: string): string | null {
  if (!email.trim()) return 'Enter your email';
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) return 'That doesn\'t look like an email address';
  return null;
}

function validatePassword(password: string): string | null {
  if (!password) return 'Enter a password';
  if (password.length < 8) return 'Password must be at least 8 characters';
  return null;
}

export default function SettingsScreen() {
  const router = useRouter();
  const [tripCount, setTripCount] = useState(0);
  const [totalStops, setTotalStops] = useState(0);
  const [prefs, setPrefs] = useState<Partial<UserPrefs>>({});
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<'signin' | 'register'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const fadeAnim  = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(16)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim,  { toValue: 1, duration: 260, useNativeDriver: true }),
      Animated.timing(slideAnim, { toValue: 0, duration: 280, useNativeDriver: true }),
    ]).start();
  }, []);

  // Signed in: count the account's trips on the server. Signed out: count the trips on this device.
  const loadStats = async (signedIn: boolean) => {
    if (signedIn) {
      try {
        const trips = await getServerTrips();
        setTripCount(trips.length);
        setTotalStops(trips.reduce((sum, t) => sum + t.stops, 0));
        return;
      } catch (e: any) {
        if (e?.status !== 401) return; // offline or server down: keep what is shown
        setUser(null);                 // session expired: fall through to the device's trips
      }
    }
    const trips = await getSavedTrips();
    setTripCount(trips.length);
    setTotalStops(trips.reduce((sum, t) => sum + t.itinerary.days.reduce((s, d) => s + d.stops.length, 0), 0));
  };

  useFocusEffect(useCallback(() => {
    getCurrentUser().then(u => {
      setUser(u);
      loadStats(!!u);
    });
    loadPrefs().then(setPrefs);
  }, []));

  const submitAccount = async () => {
    const emailErr = validateEmail(email);
    const passwordErr = validatePassword(password);
    setEmailError(emailErr);
    setPasswordError(passwordErr);
    if (emailErr || passwordErr) return;
    setAuthBusy(true); setAuthError(null);
    try {
      const u = authMode === 'register'
        ? await register(email.trim(), password)
        : await login(email.trim(), password);
      setUser(u);
      setPassword('');
      loadStats(true);
    } catch (e: any) {
      // The API layer turns 401, 409, 422 and 429 into messages that can be shown directly
      setAuthError(e?.message || 'Something went wrong. Please try again.');
    } finally { setAuthBusy(false); }
  };

  const switchAuthMode = () => {
    setAuthMode(m => m === 'signin' ? 'register' : 'signin');
    setAuthError(null);
  };

  const signOut = async () => {
    await logout();
    setUser(null);
    loadStats(false);
  };

  const confirmClearTrips = () => {
    Alert.alert('Clear saved trips', 'This will delete all your saved itineraries. This can\'t be undone.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Clear', style: 'destructive', onPress: async () => {
        await clearAllSavedTrips();
        setTripCount(0);
        setTotalStops(0);
      }},
    ]);
  };

  const confirmResetPrefs = () => {
    Alert.alert('Reset preferences', 'Your saved defaults will be cleared and reset on next trip.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Reset', style: 'destructive', onPress: async () => {
        await clearPrefs();
        setPrefs({});
      }},
    ]);
  };

  const confirmResetOnboarding = () => {
    Alert.alert('Replay onboarding', 'You\'ll see the intro screens next time you open the app.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Reset', onPress: async () => {
        await AsyncStorage.removeItem(ONBOARDING_KEY);
      }},
    ]);
  };

  const hasPrefs = Object.keys(prefs).length > 0;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backChevron}>‹</Text>
          <Text style={styles.backLabel}>Back</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Settings</Text>
        <View style={{ width: 56 }} />
      </View>

      <Animated.ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.scroll} style={{ opacity: fadeAnim, transform: [{ translateY: slideAnim }] }}>

        {/* Stats strip */}
        <View style={styles.statsRow}>
          <View style={styles.statCell}>
            <Text style={styles.statNum}>{tripCount}</Text>
            <Text style={styles.statLabel}>Trips saved</Text>
          </View>
          <View style={styles.statDivider} />
          <View style={styles.statCell}>
            <Text style={styles.statNum}>{totalStops}</Text>
            <Text style={styles.statLabel}>Stops discovered</Text>
          </View>
        </View>

        {/* Account */}
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Account</Text>
          {user ? (
            <View style={styles.card}>
              <View style={[styles.row, styles.rowBorder]}>
                <Text style={styles.rowLabel}>Signed in as</Text>
                <Text style={[styles.rowValue, styles.emailValue]} numberOfLines={1}>{user.email}</Text>
              </View>
              <TouchableOpacity style={styles.row} onPress={signOut}>
                <Text style={styles.rowLabel}>Sign out</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <>
              <View style={styles.card}>
                <TextInput
                  style={[styles.input, styles.rowBorder]}
                  placeholder="Email"
                  placeholderTextColor="rgba(255,255,255,0.25)"
                  value={email}
                  onChangeText={v => { setEmail(v); if (emailError) setEmailError(validateEmail(v)); }}
                  onBlur={() => { if (email) setEmailError(validateEmail(email)); }}
                  autoCapitalize="none" autoCorrect={false} keyboardType="email-address"
                  maxLength={320}
                />
                <TextInput
                  style={styles.input}
                  placeholder="Password (at least 8 characters)"
                  placeholderTextColor="rgba(255,255,255,0.25)"
                  value={password}
                  onChangeText={v => { setPassword(v); if (passwordError) setPasswordError(validatePassword(v)); }}
                  onBlur={() => { if (password) setPasswordError(validatePassword(password)); }}
                  onSubmitEditing={submitAccount}
                  secureTextEntry autoCapitalize="none" autoCorrect={false}
                  maxLength={72}
                />
              </View>
              {emailError && <Text style={styles.fieldError}>{emailError}</Text>}
              {passwordError && <Text style={styles.fieldError}>{passwordError}</Text>}
              {authError && <Text style={styles.fieldError}>{authError}</Text>}
              <TouchableOpacity style={styles.accountBtn} onPress={submitAccount} disabled={authBusy} activeOpacity={0.8}>
                {authBusy
                  ? <ActivityIndicator size="small" color="#09090b" />
                  : <Text style={styles.accountBtnText}>{authMode === 'register' ? 'Create account' : 'Sign in'}</Text>}
              </TouchableOpacity>
              <TouchableOpacity style={styles.dangerRow} onPress={switchAuthMode}>
                <Text style={styles.switchText}>
                  {authMode === 'register' ? 'Already have an account? Sign in' : 'New here? Create an account'}
                </Text>
              </TouchableOpacity>
              <Text style={styles.accountHint}>Sign in to see your trips on any device.</Text>
            </>
          )}
        </View>

        {/* Saved defaults */}
        {hasPrefs && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Your defaults</Text>
            <View style={styles.card}>
              {(Object.keys(PREFS_LABELS) as (keyof typeof PREFS_LABELS)[]).map((key, i, arr) => {
                const raw = prefs[key];
                if (raw === undefined) return null;
                const display = typeof raw === 'number'
                  ? raw === 10 ? 'Full day' : `${raw}h`
                  : VALUE_LABELS[raw as string] ?? raw;
                return (
                  <View key={key} style={[styles.row, i < arr.length - 1 && styles.rowBorder]}>
                    <Text style={styles.rowLabel}>{PREFS_LABELS[key]}</Text>
                    <Text style={styles.rowValue}>{display as string}</Text>
                  </View>
                );
              })}
              {prefs.goals && prefs.goals.length > 0 && (
                <View style={[styles.row, styles.goalRow]}>
                  <Text style={styles.rowLabel}>Interests</Text>
                  <View style={styles.goalPills}>
                    {prefs.goals.map(g => {
                      const color = GOAL_COLORS[g] ?? '#6B7280';
                      return (
                        <View key={g} style={[styles.goalPill, { backgroundColor: color + '18', borderColor: color + '35' }]}>
                          <View style={[styles.goalDot, { backgroundColor: color }]} />
                          <Text style={[styles.goalPillText, { color }]}>{g[0].toUpperCase() + g.slice(1)}</Text>
                        </View>
                      );
                    })}
                  </View>
                </View>
              )}
            </View>
            <TouchableOpacity style={styles.dangerRow} onPress={confirmResetPrefs}>
              <Text style={styles.dangerText}>Reset defaults</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Saved trips */}
        {tripCount > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>Data</Text>
            <View style={styles.card}>
              <TouchableOpacity style={[styles.row, !user && styles.rowBorder]} onPress={() => router.push('/history' as any)}>
                <Text style={styles.rowLabel}>View saved trips</Text>
                <Text style={styles.rowChevron}>›</Text>
              </TouchableOpacity>
              {/* Clearing only touches this device, so it is hidden while the account's trips are shown */}
              {!user && (
                <TouchableOpacity style={styles.row} onPress={confirmClearTrips}>
                  <Text style={[styles.rowLabel, { color: '#EF4444', opacity: 0.85 }]}>Clear all saved trips</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
        )}

        {/* App */}
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>App</Text>
          <View style={styles.card}>
            <TouchableOpacity style={styles.row} onPress={confirmResetOnboarding}>
              <Text style={styles.rowLabel}>Replay intro</Text>
              <Text style={styles.rowChevron}>›</Text>
            </TouchableOpacity>
          </View>
        </View>

        <Text style={styles.version}>roam — your journey, crafted for you</Text>

      </Animated.ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },

  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  backBtn:     { flexDirection: 'row', alignItems: 'center', gap: 2, width: 56 },
  backChevron: { color: 'rgba(255,255,255,0.4)', fontSize: 22, fontWeight: '300', lineHeight: 26 },
  backLabel:   { color: 'rgba(255,255,255,0.4)', fontSize: 14 },
  title:       { color: '#fff', fontSize: 16, fontWeight: '700', letterSpacing: 0.3 },

  scroll: { padding: 20, paddingBottom: 60, gap: 8 },

  // ── Stats ──
  statsRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#111113', borderRadius: 14,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
    borderTopColor: 'rgba(255,255,255,0.10)',
    marginBottom: 24, overflow: 'hidden',
  },
  statCell:    { flex: 1, alignItems: 'center', paddingVertical: 18, gap: 4 },
  statDivider: { width: 1, height: 32, backgroundColor: 'rgba(255,255,255,0.07)' },
  statNum:     { color: '#fff', fontSize: 26, fontWeight: '700', letterSpacing: -0.5 },
  statLabel:   { color: 'rgba(255,255,255,0.35)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.8 },

  section:      { gap: 4, marginBottom: 16 },
  sectionLabel: { fontSize: 11, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: 1.2, marginBottom: 4, marginLeft: 4 },

  card: {
    backgroundColor: '#111113', borderRadius: 14,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
    borderTopColor: 'rgba(255,255,255,0.10)',
    overflow: 'hidden',
  },
  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 13,
  },
  rowBorder:  { borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.05)' },
  rowLabel:   { color: 'rgba(255,255,255,0.7)', fontSize: 14 },
  rowValue:   { color: 'rgba(255,255,255,0.35)', fontSize: 14 },
  rowChevron: { color: 'rgba(255,255,255,0.25)', fontSize: 18 },

  // ── Goals row ──
  goalRow:     { alignItems: 'flex-start', flexDirection: 'column', gap: 10 },
  goalPills:   { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  goalPill:    { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 9, paddingVertical: 4, borderRadius: 100, borderWidth: 1 },
  goalDot:     { width: 5, height: 5, borderRadius: 2.5 },
  goalPillText:{ fontSize: 12, fontWeight: '500' },

  // ── Account ──
  input:          { color: '#fff', fontSize: 14, paddingHorizontal: 16, paddingVertical: 13 },
  emailValue:     { flex: 1, textAlign: 'right', marginLeft: 16 },
  fieldError:     { color: '#EF4444', fontSize: 12, marginTop: 4, marginLeft: 4, opacity: 0.85 },
  accountBtn:     { backgroundColor: '#fff', borderRadius: 12, paddingVertical: 13, alignItems: 'center', marginTop: 8 },
  accountBtnText: { color: '#09090b', fontSize: 14, fontWeight: '700', letterSpacing: 0.3 },
  switchText:     { color: 'rgba(255,255,255,0.5)', fontSize: 13 },
  accountHint:    { color: 'rgba(255,255,255,0.25)', fontSize: 12, marginLeft: 4 },

  dangerRow:  { paddingHorizontal: 4, paddingVertical: 8 },
  dangerText: { color: '#EF4444', fontSize: 13, opacity: 0.8 },

  version: { color: 'rgba(255,255,255,0.15)', fontSize: 12, textAlign: 'center', marginTop: 16 },
});
