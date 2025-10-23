// frontend/src/App.jsx
import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { DateProvider } from "./contexts/DateContext";
import Navigation from "./components/Navigation";
import Dashboard from "./pages/Dashboard";
import AspectAnalysis from "./pages/AspectAnalysis";
import ThemeAnalysis from "./pages/ThemeAnalysis";
import AIInsights from "./pages/AIInsights";
import RawData from "./pages/RawData";

export default function App() {
  return (
    <DateProvider>
      <Router>
        <div className="min-h-screen bg-slate-900 text-white">
          <Navigation />
          
                <main className="ml-64">
                  <Routes>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/aspect-analysis" element={<AspectAnalysis />} />
                    <Route path="/theme-analysis" element={<ThemeAnalysis />} />
                    <Route path="/ai-insights" element={<AIInsights />} />
                    <Route path="/raw-data" element={<RawData />} />
                  </Routes>
                </main>
        </div>
      </Router>
    </DateProvider>
  );
}
