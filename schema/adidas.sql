--
-- PostgreSQL database dump
--

\restrict Ab65RmBw6cwOyZTTKggdpf3CY2mWPNT1QMqkyfaEJFcjr4K5o9npNUwDWTZ6OoJ

-- Dumped from database version 15.14
-- Dumped by pg_dump version 15.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: appendix; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.appendix (
    appendix_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    instance_id_ref bigint NOT NULL,
    pid character varying(50),
    category character varying(50),
    division character varying(50),
    generic_product_type character varying(50),
    sale_percentage integer,
    sport character varying(50),
    unisex character varying(50),
    is_colorway boolean
);


--
-- Name: appendix_appendix_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.appendix_appendix_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: appendix_appendix_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.appendix_appendix_id_seq OWNED BY public.appendix.appendix_id;


--
-- Name: archive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.archive (
    archive_id bigint NOT NULL,
    country character varying(2) NOT NULL,
    language character varying(2) NOT NULL,
    query character varying(50) NOT NULL,
    received timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: archive_archive_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.archive_archive_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: archive_archive_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.archive_archive_id_seq OWNED BY public.archive.archive_id;


--
-- Name: cluster_fpyolo11l241114_kmeans250218; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_fpyolo11l241114_kmeans250218 (
    cluster_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    instance_id_ref bigint NOT NULL,
    segment_id_ref bigint NOT NULL,
    perc numeric(5,2),
    cluster_rank integer,
    lab_l smallint NOT NULL,
    lab_a smallint NOT NULL,
    lab_b smallint NOT NULL
);


--
-- Name: cluster_fpyolo11l241114_kmeans250218_cluster_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_fpyolo11l241114_kmeans250218_cluster_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_fpyolo11l241114_kmeans250218_cluster_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_fpyolo11l241114_kmeans250218_cluster_id_seq OWNED BY public.cluster_fpyolo11l241114_kmeans250218.cluster_id;


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comp_cluster_fpyolo11l241114_kmeans250218 (
    comp_cluster_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    perc numeric(7,2),
    lab_l smallint NOT NULL,
    lab_a smallint NOT NULL,
    lab_b smallint NOT NULL,
    n_clusters_combined integer NOT NULL
);


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218_comp_cluster_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comp_cluster_fpyolo11l241114_kmeans250218_comp_cluster_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218_comp_cluster_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comp_cluster_fpyolo11l241114_kmeans250218_comp_cluster_id_seq OWNED BY public.comp_cluster_fpyolo11l241114_kmeans250218.comp_cluster_id;


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmeans250218; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 (
    drift_color_id bigint NOT NULL,
    distance double precision NOT NULL,
    base_date date,
    compare_date date,
    base_archive_id_ref bigint,
    compare_archive_id_ref bigint,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmea_drift_color_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.driftcolor_comp_cluster_fpyolo11l241114_kmea_drift_color_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmea_drift_color_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.driftcolor_comp_cluster_fpyolo11l241114_kmea_drift_color_id_seq OWNED BY public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218.drift_color_id;


--
-- Name: family; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.family (
    family_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL
);


--
-- Name: family_family_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.family_family_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: family_family_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.family_family_id_seq OWNED BY public.family.family_id;


--
-- Name: instance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instance (
    instance_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    brand character varying(20) NOT NULL,
    title character varying(100),
    title_second character varying(100),
    price_std integer,
    price_curr integer,
    url character varying(300),
    image_url character varying(300) NOT NULL,
    page_rank integer,
    family_rank integer,
    family_id_ref bigint,
    is_colorway boolean
);


--
-- Name: instance_instance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.instance_instance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: instance_instance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.instance_instance_id_seq OWNED BY public.instance.instance_id;


--
-- Name: segment_fpyolo11l241114; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segment_fpyolo11l241114 (
    segment_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    instance_id_ref bigint NOT NULL,
    class_id integer NOT NULL,
    class_name character varying(50) NOT NULL,
    conf numeric(5,3) DEFAULT 0.0,
    is_hero boolean
);


--
-- Name: segment_fpyolo11l241114_segment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.segment_fpyolo11l241114_segment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: segment_fpyolo11l241114_segment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.segment_fpyolo11l241114_segment_id_seq OWNED BY public.segment_fpyolo11l241114.segment_id;


--
-- Name: staging_appendix; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.staging_appendix (
    appendix_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    instance_id_ref bigint NOT NULL,
    pid character varying(50),
    category character varying(50),
    division character varying(50),
    generic_product_type character varying(50),
    sale_percentage integer,
    sport character varying(50),
    unisex character varying(50),
    is_colorway boolean
);


--
-- Name: staging_appendix_appendix_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.staging_appendix_appendix_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: staging_appendix_appendix_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.staging_appendix_appendix_id_seq OWNED BY public.staging_appendix.appendix_id;


--
-- Name: staging_archive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.staging_archive (
    archive_id bigint NOT NULL,
    country character varying(2) NOT NULL,
    language character varying(2) NOT NULL,
    query character varying(50) NOT NULL,
    received timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: staging_archive_archive_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.staging_archive_archive_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: staging_archive_archive_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.staging_archive_archive_id_seq OWNED BY public.staging_archive.archive_id;


--
-- Name: staging_family; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.staging_family (
    family_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL
);


--
-- Name: staging_family_family_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.staging_family_family_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: staging_family_family_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.staging_family_family_id_seq OWNED BY public.staging_family.family_id;


--
-- Name: staging_instance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.staging_instance (
    instance_id bigint NOT NULL,
    archive_id_ref bigint NOT NULL,
    brand character varying(20) NOT NULL,
    title character varying(100),
    title_second character varying(100),
    price_std integer,
    price_curr integer,
    url character varying(300),
    image_url character varying(300) NOT NULL,
    page_rank integer,
    family_rank integer,
    family_id_ref bigint,
    is_colorway boolean
);


--
-- Name: staging_instance_instance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.staging_instance_instance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: staging_instance_instance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.staging_instance_instance_id_seq OWNED BY public.staging_instance.instance_id;


--
-- Name: appendix appendix_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appendix ALTER COLUMN appendix_id SET DEFAULT nextval('public.appendix_appendix_id_seq'::regclass);


--
-- Name: archive archive_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.archive ALTER COLUMN archive_id SET DEFAULT nextval('public.archive_archive_id_seq'::regclass);


--
-- Name: cluster_fpyolo11l241114_kmeans250218 cluster_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_fpyolo11l241114_kmeans250218 ALTER COLUMN cluster_id SET DEFAULT nextval('public.cluster_fpyolo11l241114_kmeans250218_cluster_id_seq'::regclass);


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218 comp_cluster_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp_cluster_fpyolo11l241114_kmeans250218 ALTER COLUMN comp_cluster_id SET DEFAULT nextval('public.comp_cluster_fpyolo11l241114_kmeans250218_comp_cluster_id_seq'::regclass);


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 drift_color_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 ALTER COLUMN drift_color_id SET DEFAULT nextval('public.driftcolor_comp_cluster_fpyolo11l241114_kmea_drift_color_id_seq'::regclass);


--
-- Name: family family_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.family ALTER COLUMN family_id SET DEFAULT nextval('public.family_family_id_seq'::regclass);


--
-- Name: instance instance_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instance ALTER COLUMN instance_id SET DEFAULT nextval('public.instance_instance_id_seq'::regclass);


--
-- Name: segment_fpyolo11l241114 segment_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_fpyolo11l241114 ALTER COLUMN segment_id SET DEFAULT nextval('public.segment_fpyolo11l241114_segment_id_seq'::regclass);


--
-- Name: staging_appendix appendix_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_appendix ALTER COLUMN appendix_id SET DEFAULT nextval('public.staging_appendix_appendix_id_seq'::regclass);


--
-- Name: staging_archive archive_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_archive ALTER COLUMN archive_id SET DEFAULT nextval('public.staging_archive_archive_id_seq'::regclass);


--
-- Name: staging_family family_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_family ALTER COLUMN family_id SET DEFAULT nextval('public.staging_family_family_id_seq'::regclass);


--
-- Name: staging_instance instance_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_instance ALTER COLUMN instance_id SET DEFAULT nextval('public.staging_instance_instance_id_seq'::regclass);


--
-- Name: appendix appendix_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appendix
    ADD CONSTRAINT appendix_pkey PRIMARY KEY (appendix_id);

ALTER TABLE public.appendix CLUSTER ON appendix_pkey;


--
-- Name: archive archive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.archive
    ADD CONSTRAINT archive_pkey PRIMARY KEY (archive_id);


--
-- Name: cluster_fpyolo11l241114_kmeans250218 cluster_fpyolo11l241114_kmeans250218_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT cluster_fpyolo11l241114_kmeans250218_pkey PRIMARY KEY (cluster_id);


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218 comp_cluster_fpyolo11l241114_kmeans250218_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp_cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT comp_cluster_fpyolo11l241114_kmeans250218_pkey PRIMARY KEY (comp_cluster_id);


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 driftcolor_comp_cluster_fpyolo11l241114_kmeans250218_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT driftcolor_comp_cluster_fpyolo11l241114_kmeans250218_pkey PRIMARY KEY (drift_color_id);


--
-- Name: family family_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.family
    ADD CONSTRAINT family_pkey PRIMARY KEY (family_id);


--
-- Name: instance instance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instance
    ADD CONSTRAINT instance_pkey PRIMARY KEY (instance_id);

ALTER TABLE public.instance CLUSTER ON instance_pkey;


--
-- Name: segment_fpyolo11l241114 segment_fpyolo11l241114_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_fpyolo11l241114
    ADD CONSTRAINT segment_fpyolo11l241114_pkey PRIMARY KEY (segment_id);


--
-- Name: staging_appendix staging_appendix_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_appendix
    ADD CONSTRAINT staging_appendix_pkey PRIMARY KEY (appendix_id);

ALTER TABLE public.staging_appendix CLUSTER ON staging_appendix_pkey;


--
-- Name: staging_archive staging_archive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_archive
    ADD CONSTRAINT staging_archive_pkey PRIMARY KEY (archive_id);


--
-- Name: staging_family staging_family_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_family
    ADD CONSTRAINT staging_family_pkey PRIMARY KEY (family_id);


--
-- Name: staging_instance staging_instance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_instance
    ADD CONSTRAINT staging_instance_pkey PRIMARY KEY (instance_id);

ALTER TABLE public.staging_instance CLUSTER ON staging_instance_pkey;


--
-- Name: appendix fk_appendix_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appendix
    ADD CONSTRAINT fk_appendix_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: appendix fk_appendix_instance_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.appendix
    ADD CONSTRAINT fk_appendix_instance_id_ref FOREIGN KEY (instance_id_ref) REFERENCES public.instance(instance_id);


--
-- Name: cluster_fpyolo11l241114_kmeans250218 fk_cluster_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_cluster_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: cluster_fpyolo11l241114_kmeans250218 fk_cluster_instance_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_cluster_instance_id_ref FOREIGN KEY (instance_id_ref) REFERENCES public.instance(instance_id);


--
-- Name: cluster_fpyolo11l241114_kmeans250218 fk_cluster_segment_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_cluster_segment_id_ref FOREIGN KEY (segment_id_ref) REFERENCES public.segment_fpyolo11l241114(segment_id);


--
-- Name: comp_cluster_fpyolo11l241114_kmeans250218 fk_comp_cluster_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp_cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_comp_cluster_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 fk_drift_color_base_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_drift_color_base_archive_id_ref FOREIGN KEY (base_archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: driftcolor_comp_cluster_fpyolo11l241114_kmeans250218 fk_drift_color_compare_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driftcolor_comp_cluster_fpyolo11l241114_kmeans250218
    ADD CONSTRAINT fk_drift_color_compare_archive_id_ref FOREIGN KEY (compare_archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: family fk_family_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.family
    ADD CONSTRAINT fk_family_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: instance fk_instance_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instance
    ADD CONSTRAINT fk_instance_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: instance fk_instance_family_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instance
    ADD CONSTRAINT fk_instance_family_id_ref FOREIGN KEY (family_id_ref) REFERENCES public.family(family_id);


--
-- Name: segment_fpyolo11l241114 fk_segment_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_fpyolo11l241114
    ADD CONSTRAINT fk_segment_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.archive(archive_id);


--
-- Name: segment_fpyolo11l241114 fk_segment_instance_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_fpyolo11l241114
    ADD CONSTRAINT fk_segment_instance_id_ref FOREIGN KEY (instance_id_ref) REFERENCES public.instance(instance_id);


--
-- Name: staging_appendix fk_staging_appendix_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_appendix
    ADD CONSTRAINT fk_staging_appendix_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.staging_archive(archive_id);


--
-- Name: staging_appendix fk_staging_appendix_instance_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_appendix
    ADD CONSTRAINT fk_staging_appendix_instance_id_ref FOREIGN KEY (instance_id_ref) REFERENCES public.staging_instance(instance_id);


--
-- Name: staging_family fk_staging_family_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_family
    ADD CONSTRAINT fk_staging_family_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.staging_archive(archive_id);


--
-- Name: staging_instance fk_staging_instance_archive_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_instance
    ADD CONSTRAINT fk_staging_instance_archive_id_ref FOREIGN KEY (archive_id_ref) REFERENCES public.staging_archive(archive_id);


--
-- Name: staging_instance fk_staging_instance_family_id_ref; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.staging_instance
    ADD CONSTRAINT fk_staging_instance_family_id_ref FOREIGN KEY (family_id_ref) REFERENCES public.staging_family(family_id);


--
-- PostgreSQL database dump complete
--

\unrestrict Ab65RmBw6cwOyZTTKggdpf3CY2mWPNT1QMqkyfaEJFcjr4K5o9npNUwDWTZ6OoJ

