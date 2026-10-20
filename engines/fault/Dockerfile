# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
FROM scratch

EXPOSE 8474
ENTRYPOINT ["/avenrafault"]
CMD ["-host=0.0.0.0"]

ENV LOG_LEVEL=info

COPY avenrafault-server-linux-* /avenrafault
COPY avenrafault-cli-linux-* /avenrafault-cli
