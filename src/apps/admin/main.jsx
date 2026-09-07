/* admin.wearless.kr 의 진입점. 스타일 조립만 스튜디오와 다르다(admin.css → Tailwind 레이어). */
import AppAdmin from './App.jsx';
import { mountAdminApp } from './mountAdminApp.jsx';

mountAdminApp(AppAdmin);
