import { Navigate, Route, Routes } from 'react-router-dom';
import { Header } from './components/Common/Header';
import { DataSourceProvider } from './services/dataSource';

import { OverallSituationPage } from './pages/OverallSituationPage';
import { ThreeDInspectionPage } from './pages/ThreeDInspectionPage';
import { TaskPlaybackPage } from './pages/TaskPlaybackPage';
import { EquipmentMonitoringPage } from './pages/EquipmentMonitoringPage';
import { IssueLocalizationPage } from './pages/IssueLocalizationPage';
import { DataIngestionPage } from './pages/DataIngestionPage';

import { InstrumentEvidencePage } from './pages/InstrumentEvidencePage';
import './App.css';

export default function App() {
  return (
    <DataSourceProvider>
      <div className="app-container">
        <Header />

        <main className="app-main">
          <Routes>
            <Route path="/" element={<OverallSituationPage />} />
            <Route path="/inspection" element={<ThreeDInspectionPage />} />
            <Route path="/playback" element={<TaskPlaybackPage />} />
            <Route path="/equipment" element={<EquipmentMonitoringPage />} />
            <Route path="/review" element={<IssueLocalizationPage />} />
            <Route path="/instruments" element={<InstrumentEvidencePage />} />
            <Route path="/integration" element={<DataIngestionPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>

        <footer className="app-footer-strip">Field Inspect｜开发候选 · 新功能未测试</footer>
      </div>
    </DataSourceProvider>
  );
}
