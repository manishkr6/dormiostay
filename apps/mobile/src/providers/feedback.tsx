import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AccessibilityInfo, Keyboard, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Badge, Button, type Tone } from '../components/ui';
import { tokens as t } from '../theme';
type Notice = { message: string; tone: Tone };
const Context = createContext<(notice: Notice) => void>(() => {});
export const useFeedback = () => useContext(Context);
export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [keyboard, setKeyboard] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const insets = useSafeAreaInsets();
  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (event) =>
      setKeyboard(event.endCoordinates.height),
    );
    const hide = Keyboard.addListener('keyboardDidHide', () => setKeyboard(0));
    return () => {
      show.remove();
      hide.remove();
      clearTimeout(timer.current);
    };
  }, []);
  function show(notice: Notice) {
    clearTimeout(timer.current);
    setNotice(notice);
    AccessibilityInfo.announceForAccessibility(notice.message);
    // Errors remain until dismissed; other notices use the OS-recommended accessibility timeout.
    if (notice.tone !== 'error')
      void AccessibilityInfo.getRecommendedTimeoutMillis(5000).then((duration) => {
        timer.current = setTimeout(() => setNotice(null), Math.max(duration, 5000));
      });
  }
  return (
    <Context.Provider value={show}>
      {children}
      {notice && (
        <View
          accessibilityLiveRegion="polite"
          style={{
            position: 'absolute',
            left: 16,
            right: 16,
            bottom: keyboard || Math.max(insets.bottom + 80, 96),
            backgroundColor: t.colors.surface,
            borderRadius: 16,
            borderWidth: 1,
            borderColor: t.colors.border,
            padding: 16,
            gap: 8,
          }}
        >
          <Badge label={notice.message} tone={notice.tone} />
          <Button
            label="Dismiss feedback"
            variant="ghost"
            onPress={() => {
              clearTimeout(timer.current);
              setNotice(null);
            }}
          />
        </View>
      )}
    </Context.Provider>
  );
}
