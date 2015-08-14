(function() {

  $(document).ready(function() {
    var signedRequest = $("meta[name=acpt]").attr("content");
      $.ajaxSetup({
        beforeSend: function (request) {
          request.setRequestHeader("X-acpt", signedRequest);
        }
      });
  });

})();