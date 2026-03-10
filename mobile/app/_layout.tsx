import { Stack } from 'expo-router';

export default function Layout() {
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: '#0f0f0f' } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="itinerary" options={{ presentation: 'card' }} />
    </Stack>
  );
}
