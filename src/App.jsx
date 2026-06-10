/* =============================================================
   App.jsx — routes (React Router).
   Flow: /create/input → mannequin → storyboard → generating → editor.
   "/" opens the input page directly (per product decision). Editor is
   a full-screen surface outside the app chrome (stub in phase 1).
   ============================================================= */
import { Routes, Route, Navigate } from 'react-router-dom';
import { ChromeLayout } from '@/features/shell/ChromeLayout.jsx';
import { Library } from '@/features/library/Library.jsx';
import { ProductInput } from '@/features/product-input/ProductInput.jsx';
import { Mannequin } from '@/features/mannequin/Mannequin.jsx';
import { Storyboard } from '@/features/storyboard/Storyboard.jsx';
import { Generating } from '@/features/generating/Generating.jsx';
import { Editor } from '@/features/editor/Editor.jsx';

export default function App() {
  return (
    <Routes>
      <Route element={<ChromeLayout />}>
        <Route index element={<Navigate to="/create/input" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="create">
          <Route index element={<Navigate to="/create/input" replace />} />
          <Route path="input" element={<ProductInput />} />
          <Route path="mannequin" element={<Mannequin />} />
          <Route path="storyboard" element={<Storyboard />} />
          <Route path="generating" element={<Generating />} />
        </Route>
      </Route>
      {/* editor lives outside the chrome (full-screen workspace) */}
      <Route path="editor/:id" element={<Editor />} />
      <Route path="*" element={<Navigate to="/create/input" replace />} />
    </Routes>
  );
}
