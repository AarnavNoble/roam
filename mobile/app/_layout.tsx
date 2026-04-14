import { Stack } from 'expo-router';

export default function Layout() {
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: '#09090b' },
        gestureEnabled: true,
        fullScreenGestureEnabled: true,
        animationDuration: 280,
      }}
    >
      <Stack.Screen name="index" />

      {/* Itinerary slides up from bottom — feels like revealing a result */}
      <Stack.Screen
        name="itinerary"
        options={{
          animation: 'slide_from_bottom',
          animationDuration: 380,
          gestureDirection: 'vertical',
        }}
      />

      {/* History + Settings slide in from the right */}
      <Stack.Screen
        name="history"
        options={{
          animation: 'slide_from_right',
          animationDuration: 260,
        }}
      />
      <Stack.Screen
        name="settings"
        options={{
          animation: 'slide_from_right',
          animationDuration: 260,
        }}
      />

      {/* Stop detail slides up — feels like zooming into a place */}
      <Stack.Screen
        name="stop"
        options={{
          animation: 'slide_from_bottom',
          animationDuration: 320,
          gestureDirection: 'vertical',
        }}
      />

      {/* Onboarding fades in — first launch shouldn't feel like navigation */}
      <Stack.Screen
        name="onboarding"
        options={{
          animation: 'fade',
          animationDuration: 320,
          gestureEnabled: false,
        }}
      />
    </Stack>
  );
}
