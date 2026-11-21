import { useEffect, useRef, type ReactNode, type RefObject } from 'react';
import {
  AccessibilityInfo,
  findNodeHandle,
  Keyboard,
  KeyboardAvoidingView,
  Modal as NativeModal,
  Platform,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useReducedMotion } from '../hooks/use-reduced-motion';
import { tokens as t } from '../theme';
import { Button, styles } from './ui';
type Props = {
  visible: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  returnFocus?: RefObject<View | null>;
};
export function Modal({
  visible,
  title,
  onClose,
  children,
  returnFocus,
  sheet = false,
}: Props & { sheet?: boolean }) {
  const insets = useSafeAreaInsets();
  const reduced = useReducedMotion();
  const heading = useRef<Text>(null);
  const previous = useRef(false);
  useEffect(() => {
    if (previous.current && !visible && returnFocus?.current) {
      const handle = findNodeHandle(returnFocus.current);
      if (handle) AccessibilityInfo.setAccessibilityFocus(handle);
    }
    previous.current = visible;
  }, [visible, returnFocus]);
  function close() {
    if (Keyboard.isVisible()) Keyboard.dismiss();
    else onClose();
  }
  return (
    <NativeModal
      visible={visible}
      transparent
      animationType={reduced ? 'none' : 'fade'}
      onRequestClose={close}
      onShow={() => {
        const handle = findNodeHandle(heading.current);
        if (handle) AccessibilityInfo.setAccessibilityFocus(handle);
      }}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{
          flex: 1,
          backgroundColor: t.colors.overlay,
          justifyContent: sheet ? 'flex-end' : 'center',
          paddingTop: insets.top,
          paddingBottom: sheet ? 0 : insets.bottom,
        }}
      >
        <View
          accessibilityViewIsModal
          onAccessibilityEscape={onClose}
          style={{
            maxHeight: '90%',
            marginHorizontal: sheet ? 0 : 16,
            borderRadius: t.radius.modal,
            padding: t.spacing.xl,
            paddingBottom: Math.max(insets.bottom, t.spacing.xl),
            backgroundColor: t.colors.surface,
            gap: t.spacing.lg,
          }}
        >
          <Text ref={heading} accessibilityRole="header" style={styles.heading}>
            {title}
          </Text>
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: 16 }}>
            {children}
          </ScrollView>
          <Button label={`Close ${title}`} variant="secondary" onPress={onClose} />
        </View>
      </KeyboardAvoidingView>
    </NativeModal>
  );
}
export function BottomSheet(props: Props) {
  return <Modal {...props} sheet />;
}
