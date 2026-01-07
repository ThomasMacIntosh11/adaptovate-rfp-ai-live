// frontend/src/api.js
import axios from "axios";

/**
 * Uses Render env var if available:
 *   VITE_API_BASE=https://your-backend-service.onrender.com
 *
 * Falls back to localhost for local dev.
 */
export const API_URL =
  import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export const getRFPs = (params = {}) =>
  axios.get(`${API_URL}/rfps`, { params });

export const refreshRFPs = (params = {}) =>
  axios.post(`${API_URL}/refresh`, null, { params });

export const getProgress = () =>
  axios.get(`${API_URL}/progress`);

export const getSaved = () =>
  axios.get(`${API_URL}/saved`);

export const getSavedDetail = (rfp_id) =>
  axios.get(`${API_URL}/saved/${rfp_id}`);

export const saveRFP = (rfp_id, generate_summary = true) =>
  axios.post(`${API_URL}/rfps/${rfp_id}/save`, { generate_summary });

export const deleteSaved = (rfp_id) =>
  axios.delete(`${API_URL}/saved/${rfp_id}`);

export const uploadSavedDoc = (rfp_id, file) => {
  const formData = new FormData();
  formData.append("file", file);

  return axios.post(`${API_URL}/saved/${rfp_id}/upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};

export const addNote = (rfp_id, note) =>
  axios.post(`${API_URL}/saved/${rfp_id}/notes`, { note });